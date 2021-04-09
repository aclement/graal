/*
 * Copyright (c) 2019, 2019, Oracle and/or its affiliates. All rights reserved.
 * DO NOT ALTER OR REMOVE COPYRIGHT NOTICES OR THIS FILE HEADER.
 *
 * This code is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License version 2 only, as
 * published by the Free Software Foundation.  Oracle designates this
 * particular file as subject to the "Classpath" exception as provided
 * by Oracle in the LICENSE file that accompanied this code.
 *
 * This code is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
 * version 2 for more details (a copy is included in the LICENSE file that
 * accompanied this code).
 *
 * You should have received a copy of the GNU General Public License version
 * 2 along with this work; if not, write to the Free Software Foundation,
 * Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA.
 *
 * Please contact Oracle, 500 Oracle Parkway, Redwood Shores, CA 94065 USA
 * or visit www.oracle.com if you need additional information or have any
 * questions.
 */
package com.oracle.svm.configure.config;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map.Entry;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;

import com.oracle.svm.configure.json.JsonPrintable;
import com.oracle.svm.configure.json.JsonWriter;
import com.oracle.svm.core.util.UserError;

import jdk.vm.ci.meta.JavaKind;

public class TypeConfiguration implements JsonPrintable {
    private final ConcurrentMap<String, ConfigurationType> types = new ConcurrentHashMap<>();

    public ConfigurationType get(String qualifiedJavaName) {
        return types.get(qualifiedJavaName);
    }

    public void add(ConfigurationType type) {
        ConfigurationType previous = types.putIfAbsent(type.getQualifiedJavaName(), type);
        UserError.guarantee(previous == null || previous == type, "Cannot replace existing type %s with %s", previous, type);
    }

    public ConfigurationType getOrCreateType(String qualifiedForNameString) {
        assert qualifiedForNameString.indexOf('/') == -1 : "Requires qualified Java name, not internal representation";
        assert !qualifiedForNameString.endsWith("[]") : "Requires Class.forName syntax, for example '[Ljava.lang.String;'";
        String s = qualifiedForNameString;
        int n = 0;
        while (n < s.length() && s.charAt(n) == '[') {
            n++;
        }
        if (n > 0) { // transform to Java source syntax
            StringBuilder sb = new StringBuilder(s.length() + n);
            if (s.charAt(n) == 'L' && s.charAt(s.length() - 1) == ';') {
                sb.append(s, n + 1, s.length() - 1); // cut off leading '[' and 'L' and trailing ';'
            } else if (n == s.length() - 1) {
                sb.append(JavaKind.fromPrimitiveOrVoidTypeChar(s.charAt(n)).getJavaName());
            } else {
                throw new IllegalArgumentException();
            }
            for (int i = 0; i < n; i++) {
                sb.append("[]");
            }
            s = sb.toString();
        }
        return types.computeIfAbsent(s, ConfigurationType::new);
    }

    @Override
    public void printJson(JsonWriter writer) throws IOException {
        writer.append('[');
        String prefix = "\n";
        List<ConfigurationType> list = new ArrayList<>(types.values());
        list.sort(Comparator.comparing(ConfigurationType::getQualifiedJavaName));
        for (ConfigurationType value : list) {
            writer.append(prefix);
            value.printJson(writer);
            prefix = ",\n";
        }
        writer.newline().append(']').newline();
    }

    /**
     * If something is covered by the existingConfiguration parameter it will be removed from this
     * type configuration object - leaving only the material the existing configuration does not
     * cover.
     */
    public void subtract(TypeConfiguration existingTypeConfiguration) {
        System.out.println("Number of entries in the configuration " + types.size());
        System.out.println("Number of entries in the existing configuration " + existingTypeConfiguration.types.size());

        // For extraction:
        System.out.println("For extraction: " + existingTypeConfiguration.types.keySet());

        System.out.println("Attempting subtraction of " + existingTypeConfiguration);
        List<String> toRemove = new ArrayList<>();
        for (Entry<String, ConfigurationType> entry : this.types.entrySet()) {
            System.out.println("Checking if this is in existing configuration " + entry.getKey());
            ConfigurationType configurationType = existingTypeConfiguration.get(entry.getKey());
            if (configurationType != null) {
                System.out.println("Yes it is!");
                // This means there is potentially something to subtract
                entry.getValue().subtract(configurationType);
                // After subtraction, is it safe to remove it - I suppose it is if there are no
                // flags set and no methods/fields
                ConfigurationType ct = entry.getValue();
                System.out.println("Post subtraction for " + ct.getQualifiedJavaName() + " fc=" + ct.getFieldCount() + " mc=" + ct.getMethodCount() + " cf.flags=" + ct.hasFlagsSet());
                if (ct.hasFlagsSet()) {
                    System.out.println("Flags are " + ct.toStringFlags());
                }
                if (ct.getFieldCount() == 0 && ct.getMethodCount() == 0 && !ct.hasFlagsSet()) {
                    toRemove.add(entry.getKey());
                } else {

                }
            } else {
                System.out.println("No it is!");
                // This means this is unique to the collected configuration, there is nothing remove
            }
        }
        for (String s : toRemove) {
            this.types.remove(s);
        }
        System.out.println("Number of entries in the configuration after subtraction " + types.size());
    }
}

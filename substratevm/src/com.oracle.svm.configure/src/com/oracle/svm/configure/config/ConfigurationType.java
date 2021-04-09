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
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Map.Entry;
import java.util.function.Consumer;

import com.oracle.svm.configure.json.JsonPrintable;
import com.oracle.svm.configure.json.JsonPrinter;
import com.oracle.svm.configure.json.JsonWriter;

public class ConfigurationType implements JsonPrintable {
    private final String qualifiedJavaName;

    private Map<String, FieldInfo> fields;
    private Map<ConfigurationMethod, ConfigurationMemberKind> methods;

    private boolean allDeclaredClasses;
    private boolean allPublicClasses;
    private boolean allDeclaredFields;
    private boolean allPublicFields;
    private boolean allDeclaredMethods;
    private boolean allPublicMethods;
    private boolean allDeclaredConstructors;
    private boolean allPublicConstructors;

    public int getFieldCount() {
        return fields == null ? 0 : fields.size();
    }

    public int getMethodCount() {
        return methods == null ? 0 : methods.size();
    }

    public ConfigurationType(String qualifiedJavaName) {
        assert qualifiedJavaName.indexOf('/') == -1 : "Requires qualified Java name, not internal representation";
        assert !qualifiedJavaName.startsWith("[") : "Requires Java source array syntax, for example java.lang.String[]";
        this.qualifiedJavaName = qualifiedJavaName;
    }

    public String getQualifiedJavaName() {
        return qualifiedJavaName;
    }

    public void addField(String name, ConfigurationMemberKind memberKind, boolean finalButWritable, boolean allowUnsafeAccess) {
        if (!finalButWritable && !allowUnsafeAccess) {
            if ((memberKind.includes(ConfigurationMemberKind.DECLARED) && haveAllDeclaredFields()) || (memberKind.includes(ConfigurationMemberKind.PUBLIC) && haveAllPublicFields())) {
                fields = maybeRemove(fields, map -> {
                    FieldInfo fieldInfo = map.get(name);
                    if (!fieldInfo.isFinalButWritable() && !fieldInfo.isUnsafeAccessible()) {
                        map.remove(name);
                    }
                });
                return;
            }
        }
        if (fields == null) {
            fields = new HashMap<>();
        }
        fields.compute(name, (k, v) -> (v != null)
                        ? FieldInfo.get(v.getKind().intersect(memberKind), v.isFinalButWritable() || finalButWritable, v.isUnsafeAccessible() || allowUnsafeAccess)
                        : FieldInfo.get(memberKind, finalButWritable, allowUnsafeAccess));
    }

    public void addMethodsWithName(String name, ConfigurationMemberKind memberKind) {
        addMethod(name, null, memberKind);
    }

    public void addMethod(String name, String internalSignature, ConfigurationMemberKind memberKind) {
        boolean matchesAllSignatures = (internalSignature == null);
        if (ConfigurationMethod.isConstructorName(name) ? (haveAllDeclaredConstructors() || (memberKind.includes(ConfigurationMemberKind.PUBLIC) && haveAllPublicConstructors()))
                        : ((memberKind.includes(ConfigurationMemberKind.DECLARED) && haveAllDeclaredMethods()) || (memberKind.includes(ConfigurationMemberKind.PUBLIC) && haveAllPublicMethods()))) {
            if (!matchesAllSignatures) {
                methods = maybeRemove(methods, map -> map.remove(new ConfigurationMethod(name, internalSignature)));
            }
            return;
        }
        if (methods == null) {
            methods = new HashMap<>();
        }
        ConfigurationMethod method = new ConfigurationMethod(name, internalSignature);
        if (matchesAllSignatures) { // remove any methods that the new entry matches
            methods.compute(method, (k, v) -> memberKind.union(v));
            methods = maybeRemove(methods, map -> map.entrySet().removeIf(entry -> name.equals(entry.getKey().getName()) &&
                            memberKind.includes(entry.getValue()) && !method.equals(entry.getKey())));
        } else {
            methods.compute(method, (k, v) -> memberKind.intersect(v));
        }
        assert methods.containsKey(method);
    }

    public boolean haveAllDeclaredClasses() {
        return allDeclaredClasses;
    }

    public boolean haveAllPublicClasses() {
        return allPublicClasses;
    }

    public void setAllDeclaredClasses() {
        this.allDeclaredClasses = true;
    }

    public void setAllPublicClasses() {
        this.allPublicClasses = true;
    }

    public boolean haveAllDeclaredFields() {
        return allDeclaredFields;
    }

    public boolean haveAllPublicFields() {
        return allPublicFields;
    }

    public void setAllDeclaredFields() {
        this.allDeclaredFields = true;
        removeFields(ConfigurationMemberKind.DECLARED);
    }

    public void setAllPublicFields() {
        this.allPublicFields = true;
        removeFields(ConfigurationMemberKind.PUBLIC);
    }

    public boolean haveAllDeclaredMethods() {
        return allDeclaredMethods;
    }

    public boolean haveAllPublicMethods() {
        return allPublicMethods;
    }

    public void setAllDeclaredMethods() {
        this.allDeclaredMethods = true;
        removeMethods(ConfigurationMemberKind.DECLARED, false);
    }

    public void setAllPublicMethods() {
        this.allPublicMethods = true;
        removeMethods(ConfigurationMemberKind.PUBLIC, false);
    }

    public boolean haveAllDeclaredConstructors() {
        return allDeclaredConstructors;
    }

    public boolean haveAllPublicConstructors() {
        return allPublicConstructors;
    }

    public void setAllDeclaredConstructors() {
        this.allDeclaredConstructors = true;
        removeMethods(ConfigurationMemberKind.DECLARED, true);
    }

    public void setAllPublicConstructors() {
        this.allPublicConstructors = true;
        removeMethods(ConfigurationMemberKind.PUBLIC, true);
    }

    @Override
    public void printJson(JsonWriter writer) throws IOException {
        writer.append('{').indent().newline();
        writer.quote("name").append(':').quote(qualifiedJavaName);
        optionallyPrintJsonBoolean(writer, haveAllDeclaredFields(), "allDeclaredFields");
        optionallyPrintJsonBoolean(writer, haveAllPublicFields(), "allPublicFields");
        optionallyPrintJsonBoolean(writer, haveAllDeclaredMethods(), "allDeclaredMethods");
        optionallyPrintJsonBoolean(writer, haveAllPublicMethods(), "allPublicMethods");
        optionallyPrintJsonBoolean(writer, haveAllDeclaredConstructors(), "allDeclaredConstructors");
        optionallyPrintJsonBoolean(writer, haveAllPublicConstructors(), "allPublicConstructors");
        optionallyPrintJsonBoolean(writer, haveAllDeclaredClasses(), "allDeclaredClasses");
        optionallyPrintJsonBoolean(writer, haveAllPublicClasses(), "allPublicClasses");
        if (fields != null) {
            writer.append(',').newline().quote("fields").append(':');
            JsonPrinter.printCollection(writer, fields.entrySet(), Map.Entry.comparingByKey(), ConfigurationType::printField);
        }
        if (methods != null) {
            writer.append(',').newline().quote("methods").append(':');
            JsonPrinter.printCollection(writer,
                            methods.keySet(),
                            Comparator.comparing(ConfigurationMethod::getName).thenComparing(Comparator.nullsFirst(Comparator.comparing(ConfigurationMethod::getInternalSignature))),
                            JsonPrintable::printJson);
        }
        writer.unindent().newline();
        writer.append('}');
    }

    private static void printField(Map.Entry<String, FieldInfo> entry, JsonWriter w) throws IOException {
        w.append('{').quote("name").append(':').quote(entry.getKey());
        if (entry.getValue().isFinalButWritable()) {
            w.append(", ").quote("allowWrite").append(':').append("true");
        }
        if (entry.getValue().isUnsafeAccessible()) {
            w.append(", ").quote("allowUnsafeAccess").append(':').append("true");
        }
        w.append('}');
    }

    private static void optionallyPrintJsonBoolean(JsonWriter writer, boolean predicate, String attribute) throws IOException {
        if (predicate) {
            writer.append(',').newline().quote(attribute).append(":true");
        }
    }

    private void removeFields(ConfigurationMemberKind memberKind) {
        fields = maybeRemove(fields, map -> map.values().removeIf(v -> !v.isUnsafeAccessible() && memberKind.includes(v.getKind())));
    }

    private void removeMethods(ConfigurationMemberKind memberKind, boolean constructors) {
        methods = maybeRemove(methods, map -> map.entrySet().removeIf(entry -> entry.getKey().isConstructor() == constructors && memberKind.includes(entry.getValue())));
    }

    private static <T, S> Map<T, S> maybeRemove(Map<T, S> fromMap, Consumer<Map<T, S>> action) {
        Map<T, S> map = fromMap;
        if (map != null) {
            action.accept(map);
            if (map.isEmpty()) {
                map = null;
            }
        }
        return map;
    }

    public boolean hasFlagsSet() {
        return allDeclaredClasses || allPublicClasses ||
                        allDeclaredFields || allPublicFields ||
                        allDeclaredMethods || allPublicMethods ||
                        allDeclaredConstructors || allPublicConstructors;
    }

    public String toStringFlags() {
        return "adc=" + allDeclaredClasses + " apc=" + allPublicClasses + " adf=" + allDeclaredFields + " apf=" + allPublicFields + " adm=" + allDeclaredMethods + " apm=" + allPublicMethods +
                        "adc=" + allDeclaredConstructors + " apc=" + allPublicConstructors;
    }

    public void subtract(ConfigurationType configurationType) {
        if (allDeclaredClasses && configurationType.allDeclaredClasses) {
            allDeclaredClasses = false;
        }
        if (allPublicClasses && configurationType.allPublicClasses) {
            allPublicClasses = false;
        }
        if (allDeclaredFields && configurationType.allDeclaredFields) {
            allDeclaredFields = false;
        }
        if (allPublicFields && configurationType.allPublicFields) {
            allPublicFields = false;
        }
        if (allDeclaredMethods && configurationType.allDeclaredMethods) {
            allDeclaredMethods = false;
        }
        if (allPublicMethods && configurationType.allPublicMethods) {
            allPublicMethods = false;
        }
        if (allDeclaredConstructors && configurationType.allDeclaredConstructors) {
            allDeclaredConstructors = false;
        }
        if (allPublicConstructors && configurationType.allPublicConstructors) {
            allPublicConstructors = false;
        }

        // Iterate over fields to remove, remove them if the configuration matches
        if (configurationType.fields != null && fields != null) {
            for (Map.Entry<String, FieldInfo> e : configurationType.fields.entrySet()) {
                FieldInfo fi = fields.get(e.getKey());
                if (fi.isFinalButWritable() == e.getValue().isFinalButWritable() && fi.isUnsafeAccessible() == e.getValue().isUnsafeAccessible()) {
                    System.out.println("FILTERING: Deleting field entry for " + configurationType.getQualifiedJavaName() + "." + e.getKey());
                    fields.remove(e.getKey());
                }
            }
        }
        List<String> toRemove = new ArrayList<>();
        if (fields != null) {
            for (Map.Entry<String, FieldInfo> e : fields.entrySet()) {
                FieldInfo fi = e.getValue();
                if (!fi.isFinalButWritable() && !fi.isUnsafeAccessible()) {
                    // it may be disposed if the flags for the existing entry cover it
                    ConfigurationMemberKind k = e.getValue().getKind();
                    if ((k == ConfigurationMemberKind.DECLARED || k == ConfigurationMemberKind.DECLARED_AND_PUBLIC) &&
                                    configurationType.allDeclaredFields) {
                        toRemove.add(e.getKey());
                    } else if ((k == ConfigurationMemberKind.PUBLIC || k == ConfigurationMemberKind.DECLARED_AND_PUBLIC) &&
                                    configurationType.allPublicFields) {
                        toRemove.add(e.getKey());
                    }
                }
            }
            for (String s : toRemove) {
                System.out.println("FILTERING2: Deleting field entry for " + configurationType.getQualifiedJavaName() + "." + s);
                fields.remove(s);
            }
        }
        if (configurationType.methods != null && methods != null) {
            for (Entry<ConfigurationMethod, ConfigurationMemberKind> e : configurationType.methods.entrySet()) {
                ConfigurationMethod key = e.getKey();
                ConfigurationMemberKind value = e.getValue();
                ConfigurationMemberKind exists = methods.get(key);
                if (exists != null) {
                    System.out.println("FILTERING: Deleting method entry for " + configurationType.getQualifiedJavaName() + "." + key);
                    methods.remove(key);
                } else {
                    ConfigurationMemberKind k = e.getValue();
                    if ((k == ConfigurationMemberKind.DECLARED || k == ConfigurationMemberKind.DECLARED_AND_PUBLIC) && configurationType.allDeclaredMethods) {
                        methods.remove(key);
                    } else if ((k == ConfigurationMemberKind.PUBLIC || k == ConfigurationMemberKind.DECLARED_AND_PUBLIC) &&
                                    configurationType.allPublicMethods) {
                        methods.remove(key);
                    }
                }
            }
        }
        List<ConfigurationMethod> toRemove2 = new ArrayList<>();
        if (methods != null) {
            for (Entry<ConfigurationMethod, ConfigurationMemberKind> e : methods.entrySet()) {
                // it may be disposed if the flags for the existing entry cover it
                ConfigurationMemberKind k = e.getValue();
                boolean ctor = e.getKey().isConstructor();
                if (ctor) {
                    if ((k == ConfigurationMemberKind.DECLARED || k == ConfigurationMemberKind.DECLARED_AND_PUBLIC) &&
                                    configurationType.allDeclaredConstructors) {
                        toRemove2.add(e.getKey());
                    } else if ((k == ConfigurationMemberKind.PUBLIC || k == ConfigurationMemberKind.DECLARED_AND_PUBLIC) &&
                                    configurationType.allPublicConstructors) {
                        toRemove2.add(e.getKey());
                    }
                } else {
                    if ((k == ConfigurationMemberKind.DECLARED || k == ConfigurationMemberKind.DECLARED_AND_PUBLIC) &&
                                    configurationType.allDeclaredMethods) {
                        toRemove2.add(e.getKey());
                    } else if ((k == ConfigurationMemberKind.PUBLIC || k == ConfigurationMemberKind.DECLARED_AND_PUBLIC) &&
                                    configurationType.allPublicMethods) {
                        toRemove2.add(e.getKey());
                    }
                }
            }
            for (ConfigurationMethod s : toRemove2) {
                System.out.println("FILTERING2: Deleting method entry for " + configurationType.getQualifiedJavaName() + "." + s);
                methods.remove(s);
            }
        }
    }

}

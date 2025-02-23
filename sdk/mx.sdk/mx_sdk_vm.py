#
# Copyright (c) 2019, 2020, Oracle and/or its affiliates. All rights reserved.
# DO NOT ALTER OR REMOVE COPYRIGHT NOTICES OR THIS FILE HEADER.
#
# The Universal Permissive License (UPL), Version 1.0
#
# Subject to the condition set forth below, permission is hereby granted to any
# person obtaining a copy of this software, associated documentation and/or
# data (collectively the "Software"), free of charge and under any and all
# copyright rights in the Software, and any and all patent rights owned or
# freely licensable by each licensor hereunder covering either (i) the
# unmodified Software as contributed to or provided by such licensor, or (ii)
# the Larger Works (as defined below), to deal in both
#
# (a) the Software, and
#
# (b) any piece of software and/or hardware listed in the lrgrwrks.txt file if
# one is included with the Software each a "Larger Work" to which the Software
# is contributed by such licensors),
#
# without restriction, including without limitation the rights to copy, create
# derivative works of, display, perform, and distribute the Software and make,
# use, sell, offer for sale, import, export, have made, and have sold the
# Software and the Larger Work(s), and to sublicense the foregoing rights on
# either these or other terms.
#
# This license is subject to the following condition:
#
# The above copyright notice and either this complete permission notice or at a
# minimum a reference to the UPL must be included in all copies or substantial
# portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#

from __future__ import print_function
from abc import ABCMeta

import mx
import mx_javamodules
import mx_subst
import os
import shutil
import tempfile

from os.path import join, exists, isfile, isdir, dirname, basename, relpath
from zipfile import ZipFile, ZIP_DEFLATED
from binascii import b2a_hex

from mx_javamodules import as_java_module, JavaModuleDescriptor


def _with_metaclass(meta, *bases):
    """Create a base class with a metaclass."""

    # Copyright (c) 2010-2018 Benjamin Peterson
    # Taken from six, Python compatibility library
    # MIT license

    # This requires a bit of explanation: the basic idea is to make a dummy
    # metaclass for one level of class instantiation that replaces itself with
    # the actual metaclass.
    class MetaClass(type):

        def __new__(mcs, name, this_bases, d):
            return meta(name, bases, d)

        @classmethod
        def __prepare__(mcs, name, this_bases):
            return meta.__prepare__(name, bases)
    return type.__new__(MetaClass, '_with_metaclass({}, {})'.format(meta, bases), (), {})  # pylint: disable=unused-variable


_graalvm_components = dict()  # By short_name
_graalvm_components_by_name = dict()
_vm_configs = []
_graalvm_hostvm_configs = [
    ('jvm', [], ['--jvm'], 50),
    ('jvm-no-truffle-compilation', [], ['--jvm', '--experimental-options', '--engine.Compilation=false'], 29),
    ('native', [], ['--native'], 100),
    ('native-no-truffle-compilation', [], ['--native', '--experimental-options', '--engine.Compilation=false'], 39)
]
_known_vms = set()
_base_jdk = None


class AbstractNativeImageConfig(_with_metaclass(ABCMeta, object)):
    def __init__(self, destination, jar_distributions, build_args, links=None, is_polyglot=False, dir_jars=False, home_finder=False):  # pylint: disable=super-init-not-called
        """
        :type destination: str
        :type jar_distributions: list[str]
        :type build_args: list[str]
        :type links: list[str]
        :param bool dir_jars: If true, all jars in the component directory are added to the classpath.
        """
        self.destination = mx_subst.path_substitutions.substitute(destination)
        self.jar_distributions = jar_distributions
        self.build_args = build_args
        self.links = [mx_subst.path_substitutions.substitute(link) for link in links] if links else []
        self.is_polyglot = is_polyglot
        self.dir_jars = dir_jars
        self.home_finder = home_finder

        assert isinstance(self.jar_distributions, list)
        assert isinstance(self.build_args, list)

    def __str__(self):
        return self.destination

    def __repr__(self):
        return str(self)

    @staticmethod
    def get_add_exports_list(required_exports, custom_target_module_str=None):
        add_exports = []
        for required in required_exports:
            target_modules = required_exports[required]
            target_modules_str = custom_target_module_str or ','.join(sorted(target_module.name for target_module in target_modules))
            required_module_name, required_package_name = required
            add_exports.append('--add-exports=' + required_module_name + '/' + required_package_name + "=" + target_modules_str)
        return sorted(add_exports)


class LauncherConfig(AbstractNativeImageConfig):
    def __init__(self, destination, jar_distributions, main_class, build_args, is_main_launcher=True,
                 default_symlinks=True, is_sdk_launcher=False, custom_launcher_script=None, extra_jvm_args=None,
                 is_module_launcher=False, option_vars=None, home_finder=True, **kwargs):
        """
        :param str main_class
        :param bool is_main_launcher
        :param bool default_symlinks
        :param bool is_sdk_launcher: Whether it uses org.graalvm.launcher.Launcher
        :param bool is_module_launcher: Whether it uses classpath or module-path for the application
        :param str custom_launcher_script: Custom launcher script, to be used when not compiled as a native image
        """
        super(LauncherConfig, self).__init__(destination, jar_distributions, build_args, home_finder=home_finder, **kwargs)
        self.main_class = main_class
        self.is_main_launcher = is_main_launcher
        self.module_launcher = is_module_launcher
        self.default_symlinks = default_symlinks
        self.is_sdk_launcher = is_sdk_launcher
        self.custom_launcher_script = custom_launcher_script
        self.extra_jvm_args = [] if extra_jvm_args is None else extra_jvm_args
        self.option_vars = [] if option_vars is None else option_vars

        self.relative_home_paths = {}

    def add_relative_home_path(self, language, path):
        if language in self.relative_home_paths and self.relative_home_paths[language] != path:
            raise Exception('the relative home path of {} is already set to {} and cannot also be set to {} for {}'.format(
                language, self.relative_home_paths[language], path, self.destination))
        self.relative_home_paths[language] = path

    def get_add_exports(self, missing_jars):
        if not self.module_launcher:
            return ''
        distributions = self.jar_distributions
        distributions_transitive = mx.classpath_entries(distributions)
        distributions_transitive_clean = [entry for entry in distributions_transitive if str(entry) not in missing_jars]
        required_exports = mx_javamodules.requiredExports(distributions_transitive_clean, base_jdk())
        return ' '.join(AbstractNativeImageConfig.get_add_exports_list(required_exports))


class LanguageLauncherConfig(LauncherConfig):
    def __init__(self, destination, jar_distributions, main_class, build_args, language,
                 is_sdk_launcher=True, **kwargs):
        """
        :param str language
        """
        super(LanguageLauncherConfig, self).__init__(destination, jar_distributions, main_class, build_args,
                                                     is_sdk_launcher=is_sdk_launcher, is_polyglot=False, **kwargs)
        self.language = language

        # Ensure the language launcher can always find the language home
        self.add_relative_home_path(language, relpath('.', dirname(destination)))


class LibraryConfig(AbstractNativeImageConfig):
    def __init__(self, destination, jar_distributions, build_args, jvm_library=False, **kwargs):
        """
        :param bool jvm_library
        """
        super(LibraryConfig, self).__init__(destination, jar_distributions, build_args, **kwargs)
        self.jvm_library = jvm_library


class GraalVmComponent(object):
    def __init__(self,
                 suite,
                 name,
                 short_name,
                 license_files,
                 third_party_license_files,
                 jar_distributions=None,
                 builder_jar_distributions=None,
                 support_distributions=None,
                 support_headers_distributions=None,
                 support_libraries_distributions=None,
                 dir_name=None,
                 launcher_configs=None,
                 library_configs=None,
                 provided_executables=None,
                 polyglot_lib_build_args=None,
                 polyglot_lib_jar_dependencies=None,
                 polyglot_lib_build_dependencies=None,
                 has_polyglot_lib_entrypoints=False,
                 boot_jars=None,
                 jvmci_parent_jars=None,
                 jlink=True,
                 priority=None,
                 installable=None,
                 post_install_msg=None,
                 installable_id=None,
                 dependencies=None,
                 supported=None,
                 early_adopter=False,
                 stability=None):
        """
        :param suite mx.Suite: the suite this component belongs to
        :type name: str
        :param str short_name: a short, unique name for this component
        :param str | None | False dir_name: the directory name in which this component lives. If `None`, the `short_name` is used. If `False`, files are copied to the root-dir for the component type.
        :param installable: Produce a distribution installable via `gu`
        :param post_install_msg: Post-installation message to be printed
        :param list[str] dependencies: a list of component names
        :param list[str | (str, str)] provided_executables: executables to be placed in the appropriate `bin` directory.
            In the list, strings represent a path inside the component (e.g., inside a support distribution).
            Tuples `(dist, exec)` represent an executable to be copied found in `dist`, at path `exec` (the same basename will be used).
        :type license_files: list[str]
        :type third_party_license_files: list[str]
        :type polyglot_lib_build_args: list[str]
        :type polyglot_lib_jar_dependencies: list[str]
        :type polyglot_lib_build_dependencies: list[str]
        :type has_polyglot_lib_entrypoints: bool
        :type boot_jars: list[str]
        :type jvmci_parent_jars: list[str]
        :type launcher_configs: list[LauncherConfig]
        :type library_configs: list[LibraryConfig]
        :type jar_distributions: list[str]
        :type builder_jar_distributions: list[str]
        :type support_distributions: list[str]
        :type support_headers_distributions: list[str]
        :type support_libraries_distributions: list[str]
        :param int priority: priority with a higher value means higher priority
        :type installable: bool
        :type installable_id: str
        :type post_install_msg: str
        :type stability: str | None
        """
        if dependencies is None:
            mx.logv('Component {} does not specify dependencies'.format(name))

        self.suite = suite
        self.name = name
        self.short_name = short_name
        self.dir_name = dir_name if dir_name is not None else short_name
        self.license_files = license_files
        self.third_party_license_files = third_party_license_files
        self.dependency_names = dependencies or []
        self.provided_executables = provided_executables or []
        self.polyglot_lib_build_args = polyglot_lib_build_args or []
        self.polyglot_lib_jar_dependencies = polyglot_lib_jar_dependencies or []
        self.polyglot_lib_build_dependencies = polyglot_lib_build_dependencies or []
        self.has_polyglot_lib_entrypoints = has_polyglot_lib_entrypoints
        self.boot_jars = boot_jars or []
        self.jvmci_parent_jars = jvmci_parent_jars or []
        self.jar_distributions = jar_distributions or []
        self.builder_jar_distributions = builder_jar_distributions or []
        self.support_distributions = support_distributions or []
        self.support_headers_distributions = support_headers_distributions or []
        self.support_libraries_distributions = support_libraries_distributions or []
        self.priority = priority or 0
        self.launcher_configs = launcher_configs or []
        self.library_configs = library_configs or []
        if installable is None:
            installable = isinstance(self, GraalVmLanguage)
        self.installable = installable
        self.post_install_msg = post_install_msg
        self.installable_id = installable_id or self.dir_name

        if supported is not None or early_adopter:
            if stability is not None:
                raise mx.abort("{}: Cannot use `stability` attribute in combination with deprecated `supported` and `early_adopter` attributes".format(name))
            mx.warn("{}: `supported` and `early_adopter` attributes are deprecated, please use `stability`".format(name))

        if supported:
            if early_adopter:
                stability = "earlyadopter"
            else:
                stability = "supported"
        else:
            if early_adopter:
                stability = "experimental-earlyadopter"
            else:
                stability = "experimental"
        self.stability = stability

        self.jlink = jlink

        assert isinstance(self.jar_distributions, list)
        assert isinstance(self.builder_jar_distributions, list)
        assert isinstance(self.support_distributions, list)
        assert isinstance(self.support_headers_distributions, list)
        assert isinstance(self.support_libraries_distributions, list)
        assert isinstance(self.license_files, list)
        assert isinstance(self.third_party_license_files, list)
        assert isinstance(self.provided_executables, list)
        assert isinstance(self.polyglot_lib_build_args, list)
        assert isinstance(self.polyglot_lib_jar_dependencies, list)
        assert isinstance(self.polyglot_lib_build_dependencies, list)
        assert isinstance(self.boot_jars, list)
        assert isinstance(self.jvmci_parent_jars, list)
        assert isinstance(self.launcher_configs, list)
        assert isinstance(self.library_configs, list)

        assert not any(cp_arg in self.polyglot_lib_build_args for cp_arg in ('-cp', '-classpath')), "the '{}' component passes a classpath argument to libpolylgot: '{}'. Use `polyglot_lib_jar_dependencies` instead".format(self.name, ' '.join(self.polyglot_lib_build_args))

    def __str__(self):
        return "{} ({})".format(self.name, self.dir_name)

    def direct_dependencies(self):
        try:
            return [graalvm_component_by_name(name) for name in self.dependency_names]
        except Exception as e:
            raise Exception("{} (required by {})".format(e, self.name))


class GraalVmTruffleComponent(GraalVmComponent):
    def __init__(self, suite, name, short_name, license_files, third_party_license_files, truffle_jars,
                 include_in_polyglot=True, standalone_dir_name=None, standalone_dependencies=None, **kwargs):
        """
        :param list[str] truffle_jars: JAR distributions that should be on the classpath for the language implementation.
        :param bool include_in_polyglot: whether this component is included in `--language:all` or `--tool:all` and should be part of polyglot images.
        :param str standalone_dir_name: name for the standalone archive and directory inside
        :param dict[str, (str, list[str])] standalone_dependencies: dict of dependent components to include in the standalone in the form {component name: (relative path, excluded_paths)}.
        """
        super(GraalVmTruffleComponent, self).__init__(suite, name, short_name, license_files, third_party_license_files,
                                                      jar_distributions=truffle_jars, **kwargs)
        self.include_in_polyglot = include_in_polyglot
        self.standalone_dir_name = standalone_dir_name or '{}-<version>-<graalvm_os>-<arch>'.format(self.dir_name)
        self.standalone_dependencies = standalone_dependencies or {}
        assert isinstance(self.include_in_polyglot, bool)
        assert isinstance(self.standalone_dependencies, dict)


class GraalVmLanguage(GraalVmTruffleComponent):
    pass


class GraalVmTool(GraalVmTruffleComponent):
    def __init__(self, suite, name, short_name, license_files, third_party_license_files, truffle_jars,
                 include_by_default=False, **kwargs):
        """
        :type include_by_default: bool
        """
        super(GraalVmTool, self).__init__(suite, name, short_name, license_files, third_party_license_files,
                                          truffle_jars, **kwargs)
        self.include_by_default = include_by_default


class GraalVMSvmMacro(GraalVmComponent):
    pass


class GraalVmJdkComponent(GraalVmComponent):
    pass


class GraalVmJreComponent(GraalVmComponent):
    pass


class GraalVmJvmciComponent(GraalVmJreComponent):
    def __init__(self, suite, name, short_name, license_files, third_party_license_files, jvmci_jars,
                 graal_compiler=None, **kwargs):
        """
        :type jvmci_jars: list[str]
        :type graal_compiler: str
        """
        super(GraalVmJvmciComponent, self).__init__(suite, name, short_name, license_files, third_party_license_files,
                                                    **kwargs)
        self.graal_compiler = graal_compiler
        self.jvmci_jars = jvmci_jars or []

        assert isinstance(self.jvmci_jars, list)


def register_graalvm_component(component):
    """
    :type component: GraalVmComponent
    """
    def _log_ignored_component(kept, ignored):
        """
        :type kept: GraalVmComponent
        :type ignored: GraalVmComponent
        """
        mx.logv('Suites \'{}\' and \'{}\' are registering a component with the same short name (\'{}\'), with priority \'{}\' and \'{}\' respectively.'.format(kept.suite.name, ignored.suite.name, kept.short_name, kept.priority, ignored.priority))
        mx.logv('Ignoring the one from suite \'{}\'.'.format(ignored.suite.name))

    assert ',' not in component.short_name, component.short_name
    assert ',' not in component.name, component.name

    _prev = _graalvm_components.get(component.short_name, None)
    if _prev:
        if _prev.priority == component.priority:
            mx.abort('Suites \'{}\' and \'{}\' are registering a component with the same short name (\'{}\') and priority (\'{}\')'.format(_prev.suite.name, component.suite.name, _prev.short_name, _prev.priority))
        elif _prev.priority < component.priority:
            _graalvm_components[component.short_name] = component
            del _graalvm_components_by_name[_prev.name]
            _graalvm_components_by_name[component.name] = component
            _log_ignored_component(component, _prev)
        else:
            _log_ignored_component(_prev, component)
    else:
        _graalvm_components[component.short_name] = component
        _graalvm_components_by_name[component.name] = component


def graalvm_component_by_name(name, fatalIfMissing=True):
    """
    :rtype: GraalVmComponent
    """
    if name in _graalvm_components:
        return _graalvm_components[name]
    elif name in _graalvm_components_by_name:
        return _graalvm_components_by_name[name]
    else:
        if fatalIfMissing:
            raise Exception("Unknown component: {}".format(name))
        else:
            return None


def graalvm_components(opt_limit_to_suite=False):
    """
    :rtype: list[GraalVmComponent]
    """
    if opt_limit_to_suite and mx.get_opts().specific_suites:
        return [c for c in _graalvm_components.values() if c.suite.name in mx.get_opts().specific_suites]
    else:
        return list(_graalvm_components.values())


def add_graalvm_hostvm_config(name, java_args=None, launcher_args=None, priority=0):
    """
    :type name: str
    :type java_args: list[str] | None
    :type launcher_args: list[str] | None
    :type priority: int
    """
    _graalvm_hostvm_configs.append((name, java_args, launcher_args, priority))


def register_vm_config(config_name, components, suite, dist_name=None, env_file=None):
    """
    :type config_name: str
    :type components: list[str]
    :type suite: mx.Suite
    :type dist_name: str
    :type env_file: str or None
    """
    assert config_name is not None
    assert components is not None and len(components)
    _dist_name = dist_name or config_name
    _vm_configs.append((_dist_name, config_name, components, suite, env_file))


def get_graalvm_hostvm_configs():
    return _graalvm_hostvm_configs


def register_known_vm(name):
    if name in _known_vms:
        raise mx.abort("VM '{}' already registered".format(name))
    _known_vms.add(name)


def base_jdk():
    global _base_jdk
    if _base_jdk is None:
        _base_jdk = mx.get_jdk(tag='default')
    return _base_jdk


def base_jdk_version():
    return base_jdk().javaCompliance.value


def jdk_enables_jvmci_by_default(jdk):
    """
    Gets the default value for the EnableJVMCI VM option in `jdk`.
    """
    if not hasattr(jdk, '.enables_jvmci_by_default'):
        out = mx.LinesOutputCapture()
        sink = lambda x: x
        mx.run([jdk.java, '-XX:+UnlockExperimentalVMOptions', '-XX:+PrintFlagsFinal', '-version'], out=out, err=sink)
        setattr(jdk, '.enables_jvmci_by_default', any('EnableJVMCI' in line and 'true' in line for line in out.lines))
    return getattr(jdk, '.enables_jvmci_by_default')

def jdk_has_new_jlink_options(jdk):
    """
    Determines if the jlink executable in `jdk` supports the options added by
    https://bugs.openjdk.java.net/browse/JDK-8232080.
    """
    if not hasattr(jdk, '.supports_new_jlink_options'):
        output = mx.OutputCapture()
        jlink_exe = jdk.javac.replace('javac', 'jlink')
        mx.run([jlink_exe, '--list-plugins'], out=output)
        setattr(jdk, '.supports_new_jlink_options', '--add-options=' in output.data or '--add-options ' in output.data)
    return getattr(jdk, '.supports_new_jlink_options')

def jdk_supports_enablejvmciproduct(jdk):
    """
    Determines if the jdk supports flag -XX:+EnableJVMCIProduct which isn't the case
    for some OpenJDK 11u distros.
    """
    if not hasattr(jdk, '.supports_enablejvmciproduct'):
        out = mx.LinesOutputCapture()
        sink = lambda x: x
        mx.run([jdk.java, '-XX:+UnlockExperimentalVMOptions', '-XX:+PrintFlagsFinal', '-version'], out=out, err=sink)
        setattr(jdk, '.supports_enablejvmciproduct', any('EnableJVMCIProduct' in line for line in out.lines))
    return getattr(jdk, '.supports_enablejvmciproduct')

def jdk_omits_warning_for_jlink_set_ThreadPriorityPolicy(jdk): # pylint: disable=invalid-name
    """
    Determines if the `jdk` suppresses a warning about ThreadPriorityPolicy when it
    is non-zero if the value is set from the jimage.
    https://bugs.openjdk.java.net/browse/JDK-8235908.
    """
    if not hasattr(jdk, '.omits_ThreadPriorityPolicy_warning'):
        out = mx.OutputCapture()
        sink = lambda x: x
        tmpdir = tempfile.mkdtemp(prefix='jdk_omits_warning_for_jlink_set_ThreadPriorityPolicy')
        jlink_exe = jdk.javac.replace('javac', 'jlink')
        mx.run([jlink_exe, '--add-options=-XX:ThreadPriorityPolicy=1', '--output=' + join(tmpdir, 'jdk'), '--add-modules=java.base'])
        mx.run([mx.exe_suffix(join(tmpdir, 'jdk', 'bin', 'java')), '-version'], out=sink, err=out)
        shutil.rmtree(tmpdir)
        setattr(jdk, '.omits_ThreadPriorityPolicy_warning', '-XX:ThreadPriorityPolicy=1 may require system level permission' not in out.data)
    return getattr(jdk, '.omits_ThreadPriorityPolicy_warning')

def jlink_new_jdk(jdk, dst_jdk_dir, module_dists, ignore_dists,
                  root_module_names=None,
                  missing_export_target_action='create',
                  with_source=lambda x: True,
                  vendor_info=None,
                  dedup_legal_notices=True):
    """
    Uses jlink from `jdk` to create a new JDK image in `dst_jdk_dir` with `module_dists` and
    their dependencies added to the JDK image, replacing any existing modules of the same name.

    :param JDKConfig jdk: source JDK
    :param str dst_jdk_dir: path to use for the jlink --output option
    :param list module_dists: list of distributions defining modules
    :param list ignore_dists: list of distributions that should be ignored for missing_export_target_action
    :param list root_module_names: list of strings naming the module root set for the new JDK image.
                     The named modules must either be in `module_dists` or in `jdk`. If None, then
                     the root set will be all the modules in ``module_dists` and `jdk`.
    :param str missing_export_target_action: the action to perform for a qualified export target that
                     is not present in `module_dists` and does not have a hash stored in java.base.
                     The choices are:
                       "create" - an empty module is created
                        "error" - raise an error
                           None - do nothing
    :param lambda with_source: returns True if the sources of a module distribution must be included in the new JDK
    :param dict vendor_info: values for the jlink vendor options added by JDK-8232080
    """
    assert callable(with_source)

    if jdk.javaCompliance < '9':
        mx.abort('Cannot derive a new JDK from ' + jdk.home + ' with jlink since it is not JDK 9 or later')

    exploded_java_base_module = join(jdk.home, 'modules', 'java.base')
    if exists(exploded_java_base_module):
        mx.abort('Cannot derive a new JDK from ' + jdk.home + ' since it appears to be a developer build with exploded modules')

    jimage = join(jdk.home, 'lib', 'modules')
    jmods_dir = join(jdk.home, 'jmods')
    if not isfile(jimage):
        mx.abort('Cannot derive a new JDK from ' + jdk.home + ' since ' + jimage + ' is missing or is not an ordinary file')
    if not isdir(jmods_dir):
        mx.abort('Cannot derive a new JDK from ' + jdk.home + ' since ' + jmods_dir + ' is missing or is not a directory')

    # Exclude jdk.aot due to GR-10545 and JDK-8255616
    jdk_modules = {jmd.name: jmd for jmd in jdk.get_modules() if jmd.name != 'jdk.aot'}
    modules = [as_java_module(dist, jdk) for dist in module_dists]
    all_module_names = frozenset(list(jdk_modules.keys()) + [m.name for m in modules])

    # Read hashes stored in java.base (the only module in the JDK where hashes are stored)
    out = mx.LinesOutputCapture()
    mx.run([jdk.exe_path('jmod'), 'describe', jdk_modules['java.base'].get_jmod_path()], out=out)
    lines = out.lines
    hashes = {}
    for line in lines:
        if line.startswith('hashes'):
            parts = line.split()
            assert len(parts) == 4, 'expected hashes line to have 4 fields, got {} fields: {}'.format(len(parts), line)
            _, module_name, algorithm, hash_value = parts
            hashes[module_name] = (algorithm, hash_value)

    build_dir = mx.ensure_dir_exists(join(dst_jdk_dir + ".build"))

    synthetic_modules = []
    try:
        ignore_module_names = set(mx_javamodules.get_module_name(mx.dependency(ignore_dist)) for ignore_dist in ignore_dists)
        # Synthesize modules for targets of qualified exports that are not present in `modules`.
        # Without this, runtime module resolution will fail due to missing modules.
        target_requires = {}
        for jmd in modules:
            for targets in jmd.exports.values():
                for target in targets:
                    if target not in all_module_names and target not in ignore_module_names and target not in hashes:
                        target_requires.setdefault(target, set()).add(jmd.name)
        if target_requires and missing_export_target_action is not None:
            if missing_export_target_action == 'error':
                mx.abort('Target(s) of qualified exports cannot be resolved: ' + '.'.join(target_requires.keys()))
            assert missing_export_target_action == 'create', 'invalid value for missing_export_target_action: ' + str(missing_export_target_action)

            for name, requires in target_requires.items():
                module_jar = join(build_dir, name + '.jar')
                jmd = JavaModuleDescriptor(name, {}, requires={module: [] for module in requires}, uses=set(), provides={}, jarpath=module_jar)
                synthetic_modules.append(jmd)
                module_build_dir = mx.ensure_dir_exists(join(build_dir, name))
                module_info_java = join(module_build_dir, 'module-info.java')
                module_info_class = join(module_build_dir, 'module-info.class')
                with open(module_info_java, 'w') as fp:
                    print(jmd.as_module_info(), file=fp)
                mx.run([jdk.javac, '-d', module_build_dir,
                        '--limit-modules=java.base,' + ','.join(jmd.requires.keys()),
                        '--module-path=' + os.pathsep.join((m.jarpath for m in modules)),
                        module_info_java])
                with ZipFile(module_jar, 'w') as zf:
                    zf.write(module_info_class, basename(module_info_class))
                if exists(jmd.get_jmod_path()):
                    os.remove(jmd.get_jmod_path())
                mx.run([jdk.javac.replace('javac', 'jmod'), 'create', '--class-path=' + module_build_dir, jmd.get_jmod_path()])

            modules.extend(synthetic_modules)
            all_module_names = frozenset(list(jdk_modules.keys()) + [m.name for m in modules])

        # Extract src.zip from source JDK
        jdk_src_zip = join(jdk.home, 'lib', 'src.zip')
        dst_src_zip_contents = {}
        if isfile(jdk_src_zip):
            mx.logv('[Extracting ' + jdk_src_zip + ']')
            with ZipFile(jdk_src_zip, 'r') as zf:
                for name in zf.namelist():
                    if not name.endswith('/'):
                        dst_src_zip_contents[name] = zf.read(name)
        else:
            mx.warn("'{}' does not exist or is not a file".format(jdk_src_zip))

        # Edit lib/security/default.policy in java.base
        patched_java_base = join(build_dir, 'java.base.jmod')
        with open(join(jmods_dir, 'java.base.jmod'), 'rb') as src_f, open(patched_java_base, 'wb') as dst_f:
            jmod_header = src_f.read(4)
            if len(jmod_header) != 4 or jmod_header != b'JM\x01\x00':
                raise mx.abort("Unexpected jmod header: " + b2a_hex(jmod_header).decode('ascii'))
            dst_f.write(jmod_header)
            policy_result = 'not found'
            with ZipFile(src_f, 'r') as src_zip, ZipFile(dst_f, 'w', src_zip.compression) as dst_zip:
                for i in src_zip.infolist():
                    if i.filename[-1] == '/':
                        continue
                    src_member = src_zip.read(i)
                    if i.filename == 'lib/security/default.policy':
                        policy_result = 'unmodified'
                        if 'grant codeBase "jrt:/com.oracle.graal.graal_enterprise"'.encode('utf-8') not in src_member:
                            policy_result = 'modified'
                            src_member += """
grant codeBase "jrt:/com.oracle.graal.graal_enterprise" {
    permission java.security.AllPermission;
};
""".encode('utf-8')
                        if 'grant codeBase "jrt:/org.graalvm.truffle"'.encode('utf-8') not in src_member:
                            policy_result = 'modified'
                            src_member += """
grant codeBase "jrt:/org.graalvm.truffle" {
    permission java.security.AllPermission;
};

grant codeBase "jrt:/org.graalvm.sdk" {
    permission java.security.AllPermission;
};

grant codeBase "jrt:/org.graalvm.locator" {
  permission java.io.FilePermission "<<ALL FILES>>", "read";
  permission java.util.PropertyPermission "*", "read,write";
  permission java.lang.RuntimePermission "createClassLoader";
  permission java.lang.RuntimePermission "getClassLoader";
  permission java.lang.RuntimePermission "getenv.*";
};

grant codeBase "file:${java.home}/languages/-" {
    permission java.security.AllPermission;
};
""".encode('utf-8')
                    dst_zip.writestr(i, src_member)
            if policy_result == 'not found':
                raise mx.abort("Couldn't find `lib/security/default.policy` in " + join(jmods_dir, 'java.base.jmod'))

        for jmd in modules:
            # Remove existing sources for all the modules that we include
            dst_src_zip_contents = {key: dst_src_zip_contents[key] for key in dst_src_zip_contents if not key.startswith(jmd.name)}

            if with_source(jmd.dist):
                # Add the sources that we can share.
                # Extract module sources
                jmd_src_zip = jmd.jarpath[0:-len('.jar')] + '.src.zip'
                if isfile(jmd_src_zip):
                    mx.logv('[Extracting ' + jmd_src_zip + ']')
                    with ZipFile(jmd_src_zip, 'r') as zf:
                        for name in zf.namelist():
                            if not name.endswith('/'):
                                dst_src_zip_contents[jmd.name + '/' + name] = zf.read(name)

                # Add module-info.java to sources
                dst_src_zip_contents[jmd.name + '/module-info.java'] = jmd.as_module_info(extras_as_comments=False)

        # Now build the new JDK image with jlink
        jlink = [jdk.javac.replace('javac', 'jlink')]

        if jdk_enables_jvmci_by_default(jdk):
            # On JDK 9+, +EnableJVMCI forces jdk.internal.vm.ci to be in the root set
            jlink += ['-J-XX:-EnableJVMCI', '-J-XX:-UseJVMCICompiler']
        if root_module_names is not None:
            missing = frozenset(root_module_names) - all_module_names
            if missing:
                mx.abort('Invalid module(s): {}.\nAvailable modules: {}'.format(','.join(missing), ','.join(sorted(all_module_names))))
            jlink.append('--add-modules=' + ','.join(root_module_names))
        else:
            jlink.append('--add-modules=' + ','.join(sorted(all_module_names)))

        module_path = patched_java_base + os.pathsep + jmods_dir
        if modules:
            module_path = os.pathsep.join((m.get_jmod_path(respect_stripping=True) for m in modules)) + os.pathsep + module_path
        jlink.append('--module-path=' + module_path)
        jlink.append('--output=' + dst_jdk_dir)

        # These options are derived from how OpenJDK runs jlink to produce the final runtime image.
        jlink.extend(['-J-XX:+UseSerialGC', '-J-Xms32M', '-J-Xmx512M', '-J-XX:TieredStopAtLevel=1'])
        jlink.append('-J-Dlink.debug=true')
        if dedup_legal_notices:
            jlink.append('--dedup-legal-notices=error-if-not-same-content')
        jlink.append('--keep-packaged-modules=' + join(dst_jdk_dir, 'jmods'))

        if jdk_has_new_jlink_options(jdk):
            if jdk_omits_warning_for_jlink_set_ThreadPriorityPolicy(jdk):
                thread_priority_policy_option = ' -XX:ThreadPriorityPolicy=1'
            else:
                mx.logv('[Creating JDK without -XX:ThreadPriorityPolicy=1]')
                thread_priority_policy_option = ''

            if jdk_supports_enablejvmciproduct(jdk):
                non_synthetic_modules = [m.name for m in modules if m not in synthetic_modules]
                if 'jdk.internal.vm.compiler' in non_synthetic_modules:
                    jlink.append('--add-options=-XX:+UnlockExperimentalVMOptions -XX:+EnableJVMCIProduct -XX:-UnlockExperimentalVMOptions' + thread_priority_policy_option)
                else:
                    # Don't default to using JVMCI as JIT unless Graal is being updated in the image.
                    # This avoids unexpected issues with using the out-of-date Graal compiler in
                    # the JDK itself.
                    jlink.append('--add-options=-XX:+UnlockExperimentalVMOptions -XX:+EnableJVMCIProduct -XX:-UseJVMCICompiler -XX:-UnlockExperimentalVMOptions' + thread_priority_policy_option)
            else:
                mx.logv('[Creating JDK without -XX:+EnableJVMCIProduct]')
                if thread_priority_policy_option:
                    jlink.append('--add-options=' + thread_priority_policy_option.strip())
            if vendor_info is not None:
                for name, value in vendor_info.items():
                    jlink.append('--' + name + '=' + value)

        release_file = join(jdk.home, 'release')
        if isfile(release_file):
            jlink.append('--release-info=' + release_file)

        # TODO: investigate the options below used by OpenJDK to see if they should be used:
        # --order-resources: specifies order of resources in generated lib/modules file.
        #       This is apparently not so important if a CDS archive is available.
        # --generate-jli-classes: pre-generates a set of java.lang.invoke classes.
        #       See https://github.com/openjdk/jdk/blob/master/make/GenerateLinkOptData.gmk
        mx.logv('[Creating JDK image in {}]'.format(dst_jdk_dir))
        mx.run(jlink)

        dst_src_zip = join(dst_jdk_dir, 'lib', 'src.zip')
        mx.logv('[Creating ' + dst_src_zip + ']')
        with ZipFile(dst_src_zip, 'w', compression=ZIP_DEFLATED, allowZip64=True) as zf:
            for name, contents in sorted(dst_src_zip_contents.items()):
                zf.writestr(name, contents)

        mx.logv('[Copying static libraries]')
        lib_directory = join(jdk.home, 'lib', 'static')
        if exists(lib_directory):
            dst_lib_directory = join(dst_jdk_dir, 'lib', 'static')
            try:
                mx.copytree(lib_directory, dst_lib_directory)
            except shutil.Error as e:
                # On AArch64, there can be a problem in the copystat part
                # of copytree which occurs after file and directory copying
                # has successfully completed. Since the metadata doesn't
                # matter in this case, just ensure that the content was copied.
                for root, _, lib_files in os.walk(lib_directory):
                    relative_root = os.path.relpath(root, dst_lib_directory)
                    for lib in lib_files:
                        src_lib_path = join(root, lib)
                        dst_lib_path = join(dst_lib_directory, relative_root, lib)
                        if not exists(dst_lib_path):
                            mx.abort('Error copying static libraries: {} missing in {}{}Original copytree error: {}'.format(
                                join(relative_root, lib), dst_lib_directory, os.linesep, e))
                        src_lib_hash = mx.sha1OfFile(src_lib_path)
                        dst_lib_hash = mx.sha1OfFile(dst_lib_path)
                        if src_lib_hash != dst_lib_hash:
                            mx.abort('Error copying static libraries: {} (hash={}) and {} (hash={}) differ{}Original copytree error: {}'.format(
                                src_lib_path, src_lib_hash,
                                dst_lib_path, dst_lib_hash,
                                os.linesep, e))
        # Allow older JDK versions to work
        else:
            lib_prefix = mx.add_lib_prefix('')
            lib_suffix = mx.add_static_lib_suffix('')
            lib_directory = join(jdk.home, 'lib')
            dst_lib_directory = join(dst_jdk_dir, 'lib')
            for f in os.listdir(lib_directory):
                if f.startswith(lib_prefix) and f.endswith(lib_suffix):
                    lib_path = join(lib_directory, f)
                    if isfile(lib_path):
                        shutil.copy2(lib_path, dst_lib_directory)
    finally:
        if not mx.get_opts().verbose:
            # Preserve build directory so that javac command can be re-executed
            # by cutting and pasting verbose output.
            shutil.rmtree(build_dir)

    # Create CDS archive (https://openjdk.java.net/jeps/341).
    out = mx.OutputCapture()
    mx.logv('[Creating CDS shared archive]')
    if mx.run([mx.exe_suffix(join(dst_jdk_dir, 'bin', 'java')), '-Xshare:dump', '-Xmx128M', '-Xms128M'], out=out, err=out, nonZeroIsFatal=False) != 0:
        mx.log(out.data)
        mx.abort('Error generating CDS shared archive')


def verify_graalvm_configs(suites=None):
    """
    Check the consistency of registered GraalVM configs.
    :param suites: optionally restrict the check to the configs registered by this list of suites.
    :type suites: list[str] or None
    """
    import mx_sdk_vm_impl
    child_env = os.environ.copy()
    for env_var in ['DYNAMIC_IMPORTS', 'DEFAULT_DYNAMIC_IMPORTS', 'COMPONENTS', 'EXCLUDE_COMPONENTS', 'SKIP_LIBRARIES', 'NATIVE_IMAGES', 'FORCE_BASH_LAUNCHERS', 'DISABLE_POLYGLOT', 'DISABLE_LIBPOLYGLOT']:
        if env_var in child_env:
            del child_env[env_var]
    for dist_name, _, components, suite, env_file in _vm_configs:
        if env_file is not False and (suites is None or suite.name in suites):
            _env_file = env_file or dist_name
            graalvm_dist_name = '{base_name}_{dist_name}_JAVA{jdk_version}'.format(base_name=mx_sdk_vm_impl._graalvm_base_name, dist_name=dist_name, jdk_version=mx_sdk_vm_impl._src_jdk_version).upper().replace('-', '_')
            mx.log("Checking that the env file '{}' in suite '{}' produces a GraalVM distribution named '{}'".format(_env_file, suite.name, graalvm_dist_name))
            out = mx.LinesOutputCapture()
            err = mx.LinesOutputCapture()
            retcode = mx.run_mx(['--quiet', '--no-warning', '--env', _env_file, 'graalvm-dist-name'], suite, out=out, err=err, env=child_env, nonZeroIsFatal=False)
            if retcode != 0:
                mx.abort("Unexpected return code '{}' for 'graalvm-dist-name' for env file '{}' in suite '{}'. Output:\n{}\nError:\n{}".format(retcode, _env_file, suite.name, '\n'.join(out.lines), '\n'.join(err.lines)))
            if len(out.lines) != 1 or out.lines[0] != graalvm_dist_name:
                out2 = mx.LinesOutputCapture()
                retcode2 = mx.run_mx(['--no-warning', '--env', _env_file, 'graalvm-components'], suite, out=out2, err=out2, env=child_env, nonZeroIsFatal=False)
                if retcode2 or len(out2.lines) != 1:
                    got_components = '<error>'
                    diff = ''
                else:
                    got_components = out2.lines[0]  # example string: "['bpolyglot', 'cmp']"
                    got_components_set = set(got_components[1:-1].replace('\'', '').split(', '))
                    components_set = set(components)
                    added = list(got_components_set - components_set)
                    removed = list(components_set - got_components_set)
                    diff = ('Added:\n{}\n'.format(added) if added else '') + ('Removed:\n{}\n'.format(removed) if removed else '')
                mx.abort("""\
Unexpected GraalVM dist name for env file '{}' in suite '{}'.
Expected dist name: '{}'
Actual dist name: '{}'.
Expected component list:
{}
Actual component list:
{}
{}Did you forget to update the registration of the GraalVM config?""".format(_env_file, suite.name, graalvm_dist_name, '\n'.join(out.lines + err.lines), sorted(components), got_components, diff))


register_known_vm('truffle')

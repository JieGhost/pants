# coding=utf-8
# Copyright 2016 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import (absolute_import, division, generators, nested_scopes, print_function,
                        unicode_literals, with_statement)

import os
import platform
import shutil
import subprocess
from abc import abstractmethod

from pants.util.meta import AbstractClass


#from meta import AbstractClass

class ProcessContainerWrapperFactory(object):
  """Create process container wapper based on underlying platform."""

  class UnsupportedPlatformError(Exception):
    """Raised when platform is neither Mac OS nor Linux."""

  def __new__(cls, *args, **kwargs):
    platform_system = platform.system()
    if platform_system == 'Darwin':
      return MacOSProcessContainerWrapper()
    elif platform_system == 'Linux':
      return LinuxProcessContainerWrapper()
    else:
      raise cls.UnsupportedPlatformError(
        'Process container is not supported on {}'.format(platform_system))


class ProcessContainerWrapper(AbstractClass):
  """A wrapper class for process level isolation."""

  class NotAbsolutePathError(Exception):
    """Raised when given path is not absolute."""

  def __init__(self):
    self.plain_files = set()
    self.exe_dict = dict()
    self.dirs = set()

  def _is_executable(self, file_path):
    return os.path.isfile(file_path) and os.access(file_path, os.X_OK)

  def add_plain_file(self, file_path):
    """Add path for file or dir.

    In case of dir, only the dir itself is added.
    """
    if file_path[0] != '/':
      raise self.NotAbsolutePathError('Must provide absolute path for resource file.')
    if os.path.isfile(file_path):
      self.plain_files.add(file_path)

  def add_executable(self, executable):
    if executable[0] != '/':
      raise self.NotAbsolutePathError('Must provide absolute path for binary file.')

    if self._is_executable(executable) and executable not in self.exe_dict.keys():
      dep_tuple = self.find_dependencies(executable)
      self.exe_dict[executable] = dep_tuple

  def add_files_in_dir(self, dir_path, include_deps=True):
    """Add all files under dir_path."""
    if dir_path[0] != '/':
      raise self.NotAbsolutePathError('Must provide absolute path for source dir.')

    for root, dirs, files in os.walk(dir_path):
      for f in files:
        full_path = os.path.join(root, f)
        if self._is_executable(full_path) and include_deps:
          self.add_executable(full_path)
        else:
          self.add_plain_file(full_path)

  def add_dir(self, dir_path):
    """Add everything under dir_path including dir itself."""
    if dir_path[0] != '/':
      raise self.NotAbsolutePathError('Must provide absolute path for source dir.')
    if os.path.isdir(dir_path):
      self.dirs.add(dir_path)

  def find_dependencies(self, object_file):
    """Return a list of dependencies of input object file."""
    dep_set = set()
    self._find_dependencies_helper(object_file, dep_set, object_file)
    return tuple(dep_set)

  @abstractmethod
  def _find_dependencies_helper(self,  object_file, dep_set, executable_path):
    """Recursively find all dependencies of object file."""

  @property
  def executables(self):
    return self.exe_dict.keys()

  @property
  def libraries(self):
    libset = set()
    for _, libs in self.exe_dict.items():
      libset.update(libs)
    return libset

  def all_files(self):
    """Return a list of all files in this container."""
    return sorted(self.plain_files.union(self.executables).union(self.libraries))

  @abstractmethod
  def invoke_sandbox(self, cmd):
    """Invoke sandbox tool to create sandbox."""

  # Used for creating chroot jail. May not be practical on either MacOS or Linux.
  def copy_all_files(self, target_basedir='.'):
    """Copy required resources to container."""
    for f in self.all_files():
      self._copy_force(f, os.path.join(target_basedir, f[1:]))

  def _copy_force(self, src, dst):
    if not os.path.exists(dst):
      dirname = os.path.dirname(dst)
      if not os.path.exists(dirname):
        os.makedirs(dirname)
      shutil.copy(src, dst)


class MacOSProcessContainerWrapper(ProcessContainerWrapper):
  """Process container wrapper on MacOS."""
  linker = '/usr/lib/dyld'

  def __init__(self):
    super(MacOSProcessContainerWrapper, self).__init__()
    self.binary = 'otool'
    self.args = ['-L']
    self.add_executable(self.linker)

  def add_dir_as_plain_file(self, dir_path):
    if dir_path[0] != '/':
      raise self.NotAbsolutePathError('Must provide absolute path for dir.')
    if os.path.isdir(dir_path):
      self.plain_files.add(dir_path)

  def _find_dependencies_helper(self, object_file, dep_set, executable_path):
    cmd = [self.binary] + self.args + [object_file]
    output = subprocess.check_output(cmd)
    deps = output.strip().split('\n')[1:]
    deps = [dep.strip().split()[0] for dep in deps]
    for dep in deps:
      if dep.startswith('@executable_path'):
        dep = os.path.normpath(
          os.path.join(os.path.dirname(executable_path), dep.lstrip('@executable_path').lstrip('/')))
      elif dep.startswith('@loader_path'):
        dep = os.path.normpath(os.path.join(os.path.dirname(dep), dep.lstrip('@loader_path').lstrip('/')))

      if dep not in dep_set:
        dep_set.add(dep)
        self._find_dependencies_helper(dep, dep_set, executable_path)

  def write_sb_default_header(self):
    """Default sandbox configurations when running in Mac sandbox."""
    sb = ''
    sb += '(version 1)'
    sb += '(allow default)'
    sb += '(deny file*)'
    sb += '(allow file* (literal "/"))'
    sb += '(allow file* (subpath "/dev"))'
    sb += '(allow file* (subpath "/System"))'
    sb += '(allow file* (subpath "/usr"))'
    sb += '(allow file* (subpath "/etc"))'
    sb += '(allow file* (subpath "/tmp"))'
    sb += '(allow file* (subpath "/var"))'
    sb += '(allow file* (subpath "/private"))'
    sb += '(allow file* (subpath "/bin"))'
    sb += '(allow file* (subpath "/sbin"))'
    sb += '(allow file* (subpath "/opt"))'
    sb += '(allow file* (subpath "/Library"))'

    return sb

  def write_sb_file_path(self, file_path):
    sb = ''
    while file_path != '/':
      sb += '(allow file* (literal "{}"))'.format(file_path)
      file_path = os.path.dirname(file_path)

    return sb

  def write_sb_string(self, buildroot='/'):
    sb = self.write_sb_default_header()
    sb += self.write_sb_file_path(buildroot)
    #sb += '(deny file* (subpath "/Users/yujiec/workdir/opensource/pants"))'

    # TODO (Yujie Chen): Handle symlink here
    for f in self.all_files():
      sb += '(allow file* (literal "{}"))'.format(f)

    for d in self.dirs:
      sb += '(allow file* (subpath "{}"))'.format(d)

    return sb

  def invoke_sandbox(self, cmd):
    sb = self.write_sb_string()

    p = subprocess.Popen(['sandbox-exec', '-p', sb, cmd])
    p.wait()


class LinuxProcessContainerWrapper(ProcessContainerWrapper):
  """Process container wrapper on Linux."""
  linker = 'ldd'


def main():
  pcw = ProcessContainerWrapperFactory()

  pcw.add_executable('/bin/bash')
  pcw.add_executable('/bin/ls')
  pcw.copy_all_files('foo')
  #pcw.invoke_sandbox('ls')

if __name__ == '__main__':
  main()

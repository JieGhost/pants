# coding=utf-8
# Copyright 2016 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import (absolute_import, division, generators, nested_scopes, print_function,
                        unicode_literals, with_statement)

import os
import platform
import shutil
import subprocess
from abc import abstractmethod, abstractproperty
from collections import namedtuple


try:
  from pants.util.meta import AbstractClass
except:
  from meta import AbstractClass


class ProcessContainerWrapperFactory(object):
  """Create process container wapper based on underlying platform."""

  class UnsupportedPlatformError(Exception):
    """Raised when platform is neither Mac OS nor Linux."""

  def __new__(cls, *args, **kwargs):
    platform_version = platform.platform()
    if platform_version.startswith('Darwin'):
      return MacOSProcessContainerWrapper()
    elif platform_version.startswith('Linux'):
      return LinuxProcessContainerWrapper()
    else:
      raise cls.UnsupportedPlatformError(
        'Process container is not supported on {}'.format(platform_version))


class ProcessContainerWrapper(AbstractClass):
  """A wrapper class for process level isolation."""

  class NotAbsolutePathError(Exception):
    """Raised when given path is not absolute."""

  class Executable(namedtuple('Executable', ['path', 'dependencies'])):
    """A class to represent executables."""

  def __init__(self):
    self.resources = []
    self.executables = []

  def add_resource(self, resource_file):
    if resource_file[0] != '/':
      raise self.NotAbsolutePathError('Must provide absolute path for resource file.')
    if os.path.isfile(resource_file):
      self.resources.append(resource_file)

  def add_executable(self, binary):
    if binary[0] != '/':
      raise self.NotAbsolutePathError('Must provide absolute path for binary file.')

    if os.path.isfile(binary) and os.access(binary, os.X_OK):
      dep_list = self.find_dependencies(binary)
      exe = self.Executable(binary, dep_list)
      self.executables.append(exe)

  def find_dependencies(self, object_file):
    """Return a list of dependencies of input object file."""
    dep_set = set()
    self._find_dependencies_helper(object_file, dep_set)
    return sorted(dep_set)

  @abstractmethod
  def _find_dependencies_helper(self,  object_file, dep_set):
    """Recursively find all dependencies of object file."""

  def copy_resources(self, target_basedir='.'):
    """Copy required resources to container."""
    for f in self.resources:
      self._copy_force(f, os.path.join(target_basedir, f[1:]))

    for exe in self.executables:
      self._copy_force(exe.path, os.path.join(target_basedir, exe.path[1:]))
      for dep in exe.dependencies:
        self._copy_force(dep, os.path.join(target_basedir, dep[1:]))

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

  def _find_dependencies_helper(self, object_file, dep_set):
    cmd = [self.binary] + self.args + [object_file]
    output = subprocess.check_output(cmd)
    deps = output.strip().split('\n')[1:]
    deps = [dep.strip().split()[0] for dep in deps]
    for dep in deps:
      if dep not in dep_set:
        dep_set.add(dep)
        self._find_dependencies_helper(dep, dep_set)


class LinuxProcessContainerWrapper(ProcessContainerWrapper):
  """Process container wrapper on Linux."""
  linker = 'ldd'


if __name__ == '__main__':
  pcw = ProcessContainerWrapperFactory()

  pcw.add_executable('/bin/bash')
  pcw.add_executable('/bin/ls')
  pcw.copy_resources('foo')

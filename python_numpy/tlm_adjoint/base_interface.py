#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright(c) 2018 The University of Edinburgh
#
# This file is part of tlm_adjoint.
# 
# tlm_adjoint is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, version 3 of the License.
# 
# tlm_adjoint is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
# 
# You should have received a copy of the GNU Lesser General Public License
# along with tlm_adjoint.  If not, see <https://www.gnu.org/licenses/>.

import copy
import numpy
import sys

__all__ = \
  [
    "InterfaceException",
  
    "Function",
    "FunctionSpace",
    "RealFunctionSpace",
    "ReplacementFunction",
    "apply_bcs",
    "clear_caches",
    "copy_parameters_dict",
    "finalise_adjoint_derivative_action",
    "function_assign",
    "function_axpy",
    "function_comm",
    "function_copy",
    "function_get_values",
    "function_global_size",
    "function_inner",
    "function_is_static",
    "function_linf_norm",
    "function_local_indices",
    "function_local_size",
    "function_max_value",
    "function_new",
    "function_set_values",
    "function_zero",
    "homogenized_bc",
    "info",
    "is_function",
    "replaced_function",
    "subtract_adjoint_derivative_action",
    "warning"
  ]

class InterfaceException(Exception):
  pass
  
def clear_caches():
  pass

def info(message):
  sys.stdout.write("%s\n" % message)
  sys.stdout.flush()

def warning(message):
  sys.stderr.write("%s\n" % message)
  sys.stderr.flush()

def copy_parameters_dict(parameters):
  return copy.deepcopy(parameters)
  
FunctionSpace_id_counter = [0]
class FunctionSpace:
  def __init__(self, dim):
    id = FunctionSpace_id_counter[0]
    FunctionSpace_id_counter[0] += 1
      
    self._dim = dim
    self._id = id
  
  def id(self):
    return self._id
  
  def dim(self):
    return self._dim

class RealFunctionSpace(FunctionSpace):
  def __init__(self):
    FunctionSpace.__init__(self, 1)

Function_id_counter = [0]
class Function:
  def __init__(self, space, name = None, static = False, _data = None):
    id = Function_id_counter[0]
    Function_id_counter[0] += 1
    if name is None:
      name = "f_%i" % id  # Following FEniCS 2017.2.0 behaviour
    
    self._space = space
    self._name = name
    self._static = static
    self._id = id
    self._data = numpy.zeros(space.dim(), dtype = numpy.float64) if _data is None else _data
    
  def function_space(self):
    return self._space
  
  def id(self):
    return self._id
  
  def name(self):
    return self._name
  
  def is_static(self):
    return self._static
  
  def vector(self):
    return self._data

class ReplacementFunction:
  def __init__(self, x):
    self._space = x.function_space()
    self._name = x.name()
    self._static = x.is_static()
    self._id = x.id()
    if hasattr(x, "_tlm_adjoint__tlm_basename"):
      self._tlm_adjoint__tlm_basename = x._tlm_adjoint__tlm_basename
    if hasattr(x, "_tlm_adjoint__tlm_depth"):
      self._tlm_adjoint__tlm_depth = x._tlm_adjoint__tlm_depth
    
  def function_space(self):
    return self._space
  
  def id(self):
    return self._id
  
  def name(self):
    return self._name
  
  def is_static(self):
    return self._static
    
def replaced_function(x):
  if isinstance(x, ReplacementFunction):
    return x
  if not hasattr(x, "_tlm_adjoint__ReplacementFunction"):
    x._tlm_adjoint__ReplacementFunction = ReplacementFunction(x)
  return x._tlm_adjoint__ReplacementFunction

def is_function(x):
  return isinstance(x, Function)
    
def function_is_static(x):
  return x.is_static()
  
def function_copy(x, name = None, static = False):
  return Function(x.function_space(), name = name, static = static, _data = x.vector().copy())
  
def function_assign(x, y):
  if isinstance(y, (int, float)):
    x.vector()[:] = y
  else:
    x.vector()[:] = y.vector()
    
def function_axpy(x, alpha, y):
  x.vector()[:] += alpha * y.vector()

def function_comm(x):
  import petsc4py.PETSc
  return petsc4py.PETSc.COMM_WORLD

def function_inner(x, y):
  return x.vector().dot(y.vector())

def function_local_size(x):
  return x.vector().shape[0]
  
def function_get_values(x):
  values = x.vector().view()
  values.setflags(write = False)
  return values

def function_set_values(x, values):
  x.vector()[:] = values
  
def function_max_value(x):
  return x.vector().max()
  
def function_linf_norm(x):
  return abs(x.vector()).max()
  
def function_new(x, name = None, static = False):
  return Function(x.function_space(), name = name, static = static)

def function_zero(x):
  x.vector()[:] = 0.0
  
def function_global_size(x):
  return x.vector().shape[0]

def function_local_indices(x):
  return slice(0, x.vector().shape[0])

def subtract_adjoint_derivative_action(x, y):
  if y is None:
    return
  if isinstance(y, tuple):
    alpha, y = y
    if is_function(y):
      y = y.vector()
    if alpha == 1.0:
      x.vector()[:] -= y
    else:
      x.vector()[:] -= alpha * y
  else:
    x.vector()[:] -= y.vector()
    
def finalise_adjoint_derivative_action(x):
  pass
  
def apply_bcs(x, bcs):
  if len(bcs) != 0: 
    raise InterfaceException("Unexpected boundary condition(s)")

def homogenized_bc(bc):
  raise InterfaceException("Unexpected boundary condition")

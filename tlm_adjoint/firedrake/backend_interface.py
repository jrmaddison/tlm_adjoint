from .backend import (
    LinearSolver, backend_DirichletBC, backend_Function, backend_Matrix,
    backend_assemble, backend_solve, extract_args)
from ..interface import (
    check_space_type, check_space_types, register_garbage_cleanup, space_new,
    var_space_type)

from ..patch import patch_method

from .expr import eliminate_zeros

import mpi4py.MPI as MPI
import petsc4py.PETSc as PETSc
import pyop2
import ufl

__all__ = [
    "linear_solver"
]


def _assemble(form, tensor=None, bcs=None, *,
              form_compiler_parameters=None, mat_type=None):
    if bcs is None:
        bcs = ()
    elif isinstance(bcs, backend_DirichletBC):
        bcs = (bcs,)
    if form_compiler_parameters is None:
        form_compiler_parameters = {}

    form = eliminate_zeros(form)
    if isinstance(form, ufl.classes.ZeroBaseForm):
        raise ValueError("Form cannot be a ZeroBaseForm")
    if len(form.arguments()) == 1:
        b = backend_assemble(
            form, tensor=tensor,
            form_compiler_parameters=form_compiler_parameters,
            mat_type=mat_type)
        for bc in bcs:
            bc.apply(b.riesz_representation("l2"))
    else:
        b = backend_assemble(
            form, tensor=tensor, bcs=bcs,
            form_compiler_parameters=form_compiler_parameters,
            mat_type=mat_type)

    return b


def _assemble_system(A_form, b_form=None, bcs=None, *,
                     form_compiler_parameters=None, mat_type=None):
    if bcs is None:
        bcs = ()
    elif isinstance(bcs, backend_DirichletBC):
        bcs = (bcs,)
    if form_compiler_parameters is None:
        form_compiler_parameters = {}

    A = _assemble(
        A_form, bcs=bcs, form_compiler_parameters=form_compiler_parameters,
        mat_type=mat_type)

    if len(bcs) > 0:
        F = backend_Function(A_form.arguments()[0].function_space())
        for bc in bcs:
            bc.apply(F)

        if b_form is None:
            b = _assemble(
                -ufl.action(A_form, F), bcs=bcs,
                form_compiler_parameters=form_compiler_parameters,
                mat_type=mat_type)

            with b.dat.vec_ro as b_v:
                if b_v.norm(norm_type=PETSc.NormType.NORM_INFINITY) == 0.0:
                    b = None
        else:
            b = _assemble(
                b_form - ufl.action(A_form, F), bcs=bcs,
                form_compiler_parameters=form_compiler_parameters,
                mat_type=mat_type)
    else:
        if b_form is None:
            b = None
        else:
            b = _assemble(
                b_form,
                form_compiler_parameters=form_compiler_parameters,
                mat_type=mat_type)

    A._tlm_adjoint__lift_bcs = False

    return A, b


@patch_method(LinearSolver, "_lifted")
def LinearSolver_lifted(self, orig, orig_args, b):
    if getattr(self.A, "_tlm_adjoint__lift_bcs", True):
        return orig_args()
    else:
        return b


def assemble_matrix(form, bcs=None, *,
                    form_compiler_parameters=None, mat_type=None):
    if bcs is None:
        bcs = ()
    elif isinstance(bcs, backend_DirichletBC):
        bcs = (bcs,)
    if form_compiler_parameters is None:
        form_compiler_parameters = {}

    return _assemble_system(form, bcs=bcs,
                            form_compiler_parameters=form_compiler_parameters,
                            mat_type=mat_type)


def assemble(form, tensor=None, bcs=None, *,
             form_compiler_parameters=None, mat_type=None):
    if form_compiler_parameters is None:
        form_compiler_parameters = {}

    b = _assemble(
        form, tensor=tensor, bcs=bcs,
        form_compiler_parameters=form_compiler_parameters, mat_type=mat_type)

    return b


def matrix_copy(A):
    if not isinstance(A, backend_Matrix):
        raise TypeError("Unexpected matrix type")

    options_prefix = A.petscmat.getOptionsPrefix()
    A_copy = backend_Matrix(A.a, A.bcs, A.mat_type,
                            A.M.sparsity, A.M.dtype,
                            options_prefix=options_prefix)

    assert A.petscmat.assembled
    A_copy.petscmat.axpy(1.0, A.petscmat)
    assert A_copy.petscmat.assembled

    # MatAXPY does not propagate the options prefix
    A_copy.petscmat.setOptionsPrefix(options_prefix)

    if hasattr(A, "_tlm_adjoint__lift_bcs"):
        A_copy._tlm_adjoint__lift_bcs = A._tlm_adjoint__lift_bcs

    return A_copy


def matrix_multiply(A, x, *,
                    tensor=None, addto=False, action_type="conjugate_dual"):
    if tensor is None:
        tensor = space_new(
            A.a.arguments()[0].function_space(),
            space_type=var_space_type(x, rel_space_type=action_type))
    else:
        check_space_types(tensor, x, rel_space_type=action_type)

    if addto:
        with x.dat.vec_ro as x_v, tensor.dat.vec as tensor_v:
            A.petscmat.multAdd(x_v, tensor_v, tensor_v)
    else:
        with x.dat.vec_ro as x_v, tensor.dat.vec_wo as tensor_v:
            A.petscmat.mult(x_v, tensor_v)

    return tensor


def assemble_linear_solver(A_form, b_form=None, bcs=None, *,
                           form_compiler_parameters=None,
                           linear_solver_parameters=None):
    if bcs is None:
        bcs = ()
    elif isinstance(bcs, backend_DirichletBC):
        bcs = (bcs,)
    if form_compiler_parameters is None:
        form_compiler_parameters = {}
    if linear_solver_parameters is None:
        linear_solver_parameters = {}

    A, b = _assemble_system(
        A_form, b_form=b_form, bcs=bcs,
        form_compiler_parameters=form_compiler_parameters,
        mat_type=linear_solver_parameters.get("mat_type", None))

    solver = linear_solver(A, linear_solver_parameters)

    return solver, A, b


def linear_solver(A, linear_solver_parameters):
    """Construct a :class:`firedrake.linear_solver.LinearSolver`.

    :arg A: A :class:`firedrake.matrix.Matrix`.
    :arg linear_solver_parameters: Linear solver parameters.
    :returns: The :class:`firedrake.linear_solver.LinearSolver`.
    """

    if "tlm_adjoint" in linear_solver_parameters:
        linear_solver_parameters = dict(linear_solver_parameters)
        tlm_adjoint_parameters = linear_solver_parameters.pop("tlm_adjoint")
        options_prefix = tlm_adjoint_parameters.get("options_prefix", None)
        nullspace = tlm_adjoint_parameters.get("nullspace", None)
        transpose_nullspace = tlm_adjoint_parameters.get("transpose_nullspace",
                                                         None)
        near_nullspace = tlm_adjoint_parameters.get("near_nullspace", None)
    else:
        options_prefix = None
        nullspace = None
        transpose_nullspace = None
        near_nullspace = None
    return LinearSolver(A, solver_parameters=linear_solver_parameters,
                        options_prefix=options_prefix,
                        nullspace=nullspace,
                        transpose_nullspace=transpose_nullspace,
                        near_nullspace=near_nullspace)


def solve(*args, **kwargs):
    if not isinstance(args[0], ufl.classes.Equation):
        return backend_solve(*args, **kwargs)

    eq, x, bcs, J, Jp, M, form_compiler_parameters, solver_parameters, \
        nullspace, transpose_nullspace, near_nullspace, options_prefix = \
        extract_args(*args, **kwargs)
    check_space_type(x, "primal")
    if bcs is None:
        bcs = ()
    elif isinstance(bcs, backend_DirichletBC):
        bcs = (bcs,)
    if form_compiler_parameters is None:
        form_compiler_parameters = {}
    if solver_parameters is None:
        solver_parameters = {}

    if "tlm_adjoint" in solver_parameters:
        solver_parameters = dict(solver_parameters)
        tlm_adjoint_parameters = solver_parameters.pop("tlm_adjoint")

        if "options_prefix" in tlm_adjoint_parameters:
            if options_prefix is not None:
                raise TypeError("Cannot pass both options_prefix argument and "
                                "solver parameter")
            options_prefix = tlm_adjoint_parameters["options_prefix"]

        if "nullspace" in tlm_adjoint_parameters:
            if nullspace is not None:
                raise TypeError("Cannot pass both nullspace argument and "
                                "solver parameter")
            nullspace = tlm_adjoint_parameters["nullspace"]

        if "transpose_nullspace" in tlm_adjoint_parameters:
            if transpose_nullspace is not None:
                raise TypeError("Cannot pass both transpose_nullspace "
                                "argument and solver parameter")
            transpose_nullspace = tlm_adjoint_parameters["transpose_nullspace"]

        if "near_nullspace" in tlm_adjoint_parameters:
            if near_nullspace is not None:
                raise TypeError("Cannot pass both near_nullspace argument and "
                                "solver parameter")
            near_nullspace = tlm_adjoint_parameters["near_nullspace"]

    return backend_solve(eq, x, bcs, J=J, Jp=Jp, M=M,
                         form_compiler_parameters=form_compiler_parameters,
                         solver_parameters=solver_parameters,
                         nullspace=nullspace,
                         transpose_nullspace=transpose_nullspace,
                         near_nullspace=near_nullspace,
                         options_prefix=options_prefix)


def garbage_cleanup_internal_comm(comm):
    if not MPI.Is_finalized() and not PETSc.Sys.isFinalized() \
            and not pyop2.mpi.PYOP2_FINALIZED \
            and comm.py2f() != MPI.COMM_NULL.py2f():
        if pyop2.mpi.is_pyop2_comm(comm):
            raise RuntimeError("Should not call garbage_cleanup directly on a "
                               "PyOP2 communicator")
        internal_comm = comm.Get_attr(pyop2.mpi.innercomm_keyval)
        if internal_comm is not None and internal_comm.py2f() != MPI.COMM_NULL.py2f():  # noqa: E501
            PETSc.garbage_cleanup(internal_comm)


register_garbage_cleanup(garbage_cleanup_internal_comm)

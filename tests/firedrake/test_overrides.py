#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from firedrake import *
from tlm_adjoint.firedrake import *
from tlm_adjoint.firedrake.backend import backend_assemble
from tlm_adjoint.firedrake.backend_code_generator_interface import (
    assemble as backend_code_generator_interface_assemble)

from .test_base import *

import numpy as np
import pytest
import ufl

pytestmark = pytest.mark.skipif(
    DEFAULT_COMM.size not in {1, 4},
    reason="tests must be run in serial, or with 4 processes")


@pytest.mark.firedrake
@seed_test
def test_overrides(setup_test, test_leaks):
    mesh = UnitSquareMesh(20, 20)
    X = SpatialCoordinate(mesh)
    space = FunctionSpace(mesh, "Lagrange", 1)
    test, trial = TestFunction(space), TrialFunction(space)

    F = Function(space, name="F", static=True)
    interpolate_expression(F, sin(pi * X[0]) * sin(3.0 * pi * X[1]))

    bc = DirichletBC(space, 1.0, "on_boundary")

    def project_project(F):
        return project(F, space, bcs=bc, name="G",
                       solver_parameters=ls_parameters_cg)

    def project_assemble_LinearSolver(F):
        G = Function(space, name="G")

        A = assemble(inner(trial, test) * dx, bcs=bc)
        b = assemble(inner(F, test) * dx)

        solver = LinearSolver(A, solver_parameters=ls_parameters_cg)
        solver.solve(G, b)

        return G

    def project_LinearVariationalSolver(F):
        G = Function(space, name="G")

        eq = inner(trial, test) * dx == inner(F, test) * dx
        problem = LinearVariationalProblem(eq.lhs, eq.rhs, G, bcs=bc)
        solver = LinearVariationalSolver(
            problem, solver_parameters=ls_parameters_cg)
        solver.solve()

        return G

    def project_NonlinearVariationalSolver(F):
        G = Function(space, name="G")

        eq = inner(G, test) * dx - inner(F, test) * dx
        problem = NonlinearVariationalProblem(eq, G,
                                              J=inner(trial, test) * dx,
                                              bcs=bc)
        solver = NonlinearVariationalSolver(
            problem, solver_parameters=ns_parameters_newton_cg)
        solver.solve()

        return G

    for project_fn in [project_project,
                       project_assemble_LinearSolver,
                       project_LinearVariationalSolver,
                       project_NonlinearVariationalSolver]:
        def forward(F):
            G = project_fn(F)

            J = Functional(name="J")
            J.assign(dot(G, G * (1 + G)) * dx)
            return G, J

        reset_manager("memory", {"drop_references": True})
        start_manager()
        G, J = forward(F)
        stop_manager()

        error = Function(space, name="error")
        solve(inner(trial, test) * dx == inner(F, test) * dx,
              error, bc, solver_parameters=ls_parameters_cg)
        function_axpy(error, -1.0, G)
        assert function_linf_norm(error) < 1.0e-14

        J_val = J.value()

        dJ = compute_gradient(J, F)

        def forward_J(F):
            return forward(F)[1]

        min_order = taylor_test(forward_J, F, J_val=J_val, dJ=dJ)
        assert min_order > 2.00

        ddJ = Hessian(forward_J)
        min_order = taylor_test(forward_J, F, J_val=J_val, ddJ=ddJ)
        assert min_order > 2.99

        min_order = taylor_test_tlm(forward_J, F, tlm_order=1)
        assert min_order > 2.00

        min_order = taylor_test_tlm_adjoint(forward_J, F, adjoint_order=1)
        assert min_order > 2.00

        min_order = taylor_test_tlm_adjoint(forward_J, F, adjoint_order=2)
        assert min_order > 1.99


@pytest.mark.firedrake
@seed_test
def test_Function_assign(setup_test, test_leaks):
    mesh = UnitIntervalMesh(10)
    space = FunctionSpace(mesh, "Lagrange", 1)

    def forward(m):
        u = Constant(0.0, name="u")
        u.assign(m)
        u.assign(-2.0)
        u.assign(u + 2.0 * m)

        v = Function(space, name="v")
        v.assign(u)
        v.assign(u + Constant(1.0))
        v.assign(0.0)
        v.assign(u + v + Constant(1.0))
        v.assign(2.5 * u + 3.6 * v + 4.7 * m)

        J = Functional(name="J")
        J.assign(((v - 1.0) ** 4) * dx)
        return J

    m = Constant(2.0, name="m")

    start_manager()
    J = forward(m)
    stop_manager()

    J_val = J.value()
    assert abs(J_val - 342974.2096) < 1.0e-9

    dJ = compute_gradient(J, m)

    dm = Constant(1.0)

    min_order = taylor_test(forward, m, J_val=J_val, dJ=dJ, dM=dm)
    assert min_order > 2.00

    ddJ = Hessian(forward)
    min_order = taylor_test(forward, m, J_val=J_val, ddJ=ddJ, dM=dm)
    assert min_order > 3.00

    min_order = taylor_test_tlm(forward, m, tlm_order=1, dMs=(dm,))
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward, m, adjoint_order=1,
                                        dMs=(dm,))
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward, m, adjoint_order=2,
                                        dMs=(dm, dm))
    assert min_order > 2.00


@pytest.mark.firedrake
@seed_test
def test_Nullspace(setup_test, test_leaks):
    mesh = UnitSquareMesh(20, 20)
    X = SpatialCoordinate(mesh)
    space = FunctionSpace(mesh, "Lagrange", 1)
    test, trial = TestFunction(space), TrialFunction(space)

    def forward(F):
        psi = Function(space, name="psi")

        solve(inner(grad(trial), grad(test)) * dx
              == -inner(F * F, test) * dx, psi,
              solver_parameters=ls_parameters_cg,
              nullspace=VectorSpaceBasis(constant=True, comm=function_comm(psi)),  # noqa: E501
              transpose_nullspace=VectorSpaceBasis(constant=True, comm=function_comm(psi)))  # noqa: E501

        J = Functional(name="J")
        J.assign((dot(psi, psi) ** 2) * dx
                 + dot(grad(psi), grad(psi)) * dx)

        return psi, J

    F = Function(space, name="F", static=True)
    interpolate_expression(F, sqrt(sin(pi * X[1])))

    start_manager()
    psi, J = forward(F)
    stop_manager()

    assert abs(function_sum(psi)) < 1.0e-15

    J_val = J.value()

    dJ = compute_gradient(J, F)

    def forward_J(F):
        return forward(F)[1]

    min_order = taylor_test(forward_J, F, J_val=J_val, dJ=dJ)
    assert min_order > 2.00

    ddJ = Hessian(forward_J)
    min_order = taylor_test(forward_J, F, J_val=J_val, ddJ=ddJ)
    assert min_order > 2.99

    min_order = taylor_test_tlm(forward_J, F, tlm_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward_J, F, adjoint_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward_J, F, adjoint_order=2)
    assert min_order > 2.00


@pytest.mark.firedrake
@pytest.mark.parametrize("degree", [1, 2, 3])
@seed_test
def test_interpolate(setup_test, test_leaks,
                     degree):
    mesh = UnitIntervalMesh(20)
    X = SpatialCoordinate(mesh)
    space_1 = FunctionSpace(mesh, "Lagrange", 1)
    space_2 = FunctionSpace(mesh, "Lagrange", degree)

    y_2 = Function(space_2, name="y_2")
    if complex_mode:
        interpolate_expression(y_2,
                               cos(3.0 * pi * X[0])
                               + 1.0j * sin(5.0 * pi * X[0]))
    else:
        interpolate_expression(y_2,
                               cos(3.0 * pi * X[0]))
    y_1_ref = Function(space_1, name="y_1_ref")
    y_1_ref.interpolate(y_2)

    def interpolate_interpolate(v, V):
        return interpolate(v, V)

    def interpolate_Function_interpolate(v, V):
        x = space_new(V)
        x.interpolate(v)
        return x

    def interpolate_Interpolator_function(v, V):
        interp = Interpolator(v, V)
        x = space_new(V)
        interp.interpolate(output=x)
        return x

    def interpolate_Interpolator_test(v, V):
        interp = Interpolator(TestFunction(function_space(v)), V)
        x = space_new(V)
        interp.interpolate(v, output=x)
        return x

    for interpolate_fn in [interpolate_interpolate,
                           interpolate_Function_interpolate,
                           interpolate_Interpolator_function,
                           interpolate_Interpolator_test]:
        def forward(y_2):
            y_1 = interpolate_fn(y_2, space_1)

            J = Functional(name="J")
            J.assign(((y_1 - Constant(1.0)) ** 4) * dx)
            return y_1, J

        reset_manager("memory", {"drop_references": True})
        start_manager()
        y_1, J = forward(y_2)
        stop_manager()

        y_1_error = function_copy(y_1)
        function_axpy(y_1_error, -1.0, y_1_ref)
        assert function_linf_norm(y_1_error) < 1.0e-14

        J_val = J.value()

        dJ = compute_gradient(J, y_2)

        def forward_J(y_2):
            _, J = forward(y_2)
            return J

        min_order = taylor_test(forward_J, y_2, J_val=J_val, dJ=dJ)
        assert min_order > 1.99

        ddJ = Hessian(forward_J)
        min_order = taylor_test(forward_J, y_2, J_val=J_val, ddJ=ddJ)
        assert min_order > 2.99

        min_order = taylor_test_tlm(forward_J, y_2, tlm_order=1)
        assert min_order > 1.99

        min_order = taylor_test_tlm_adjoint(forward_J, y_2, adjoint_order=1)
        assert min_order > 1.99

        min_order = taylor_test_tlm_adjoint(forward_J, y_2, adjoint_order=2)
        assert min_order > 1.99


@pytest.mark.firedrake
@pytest.mark.skipif(complex_mode, reason="real only")
@seed_test
def test_Assemble_arity_1(setup_test, test_leaks):
    mesh = UnitSquareMesh(20, 20)
    X = SpatialCoordinate(mesh)
    space = FunctionSpace(mesh, "Lagrange", 1)
    test = TestFunction(space)

    def forward(F):
        x = assemble(inner(ufl.conj(F ** 3), test) * dx)

        J = Functional(name="J")
        InnerProduct(J.function(), F, x).solve()
        return J

    F = Function(space, name="F", static=True)
    interpolate_expression(F, X[0] * sin(pi * X[1]))

    start_manager()
    J = forward(F)
    stop_manager()

    J_val = J.value()
    assert abs(J_val - assemble((F ** 4) * dx)) < 1.0e-16

    dJ = compute_gradient(J, F)

    min_order = taylor_test(forward, F, J_val=J_val, dJ=dJ)
    assert min_order > 2.00

    ddJ = Hessian(forward)
    min_order = taylor_test(forward, F, J_val=J_val, ddJ=ddJ)
    assert min_order > 3.00

    min_order = taylor_test_tlm(forward, F, tlm_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward, F, adjoint_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward, F, adjoint_order=2)
    assert min_order > 2.00


@pytest.mark.firedrake
@pytest.mark.parametrize("ZeroFunction", [Function, ZeroFunction])
@pytest.mark.parametrize("assemble", [backend_assemble,
                                      assemble,
                                      backend_code_generator_interface_assemble])  # noqa: E501
@seed_test
def test_assemble_ZeroFunction(setup_test, test_leaks,
                               ZeroFunction, assemble):
    mesh = UnitIntervalMesh(10)
    space = FunctionSpace(mesh, "Lagrange", 1)

    F = ZeroFunction(space, name="F")
    G = Function(space, name="G")

    form = (F + G) * dx

    b = assemble(form)
    assert abs(b) == 0.0

    G.assign(np.sqrt(2.0))
    b_ref = backend_assemble(form)
    assert abs(b_ref - np.sqrt(2.0)) < 1.0e-15

    for _ in range(3):
        b = assemble(form)
        assert abs(b - b_ref) < 1.0e-15

    G = Function(space, name="G")
    F = ZeroFunction(space, name="F")

    form = (F + G) * dx

    b = assemble(form)
    assert abs(b) == 0.0

    G.assign(np.sqrt(2.0))
    b_ref = backend_assemble(form)
    assert abs(b_ref - np.sqrt(2.0)) < 1.0e-15

    for _ in range(3):
        b = assemble(form)
        assert abs(b - b_ref) < 1.0e-15


@pytest.mark.firedrake
@seed_test
def test_DirichletBC_apply(setup_test, test_leaks, tmp_path):
    configure_checkpointing("periodic_disk", {"period": 1})

    mesh = UnitSquareMesh(10, 10)
    X = SpatialCoordinate(mesh)
    space = FunctionSpace(mesh, "Lagrange", 2)

    def forward(y):
        x = Function(space, name="x")
        x.assign(2 * y)
        DirichletBC(space, y, "on_boundary").apply(x)

        y_bc = Function(space, name="y_bc")
        DirichletBC(space, y, "on_boundary").apply(y_bc)

        assert np.sqrt(abs(assemble(inner(x + y_bc - 2 * y,
                                          x + y_bc - 2 * y) * dx))) < 1.0e-15
        assert np.sqrt(abs(assemble(inner(x - y, x - y) * ds))) < 1.0e-15
        assert np.sqrt(abs(assemble(inner(y_bc - y, y_bc - y) * ds))) < 1.0e-15

        J = Functional(name="J")
        J.assign(((x - Constant(1.0)) ** 3) * ds
                 + ((x + y_bc) ** 3) * dx)
        return J

    y = Function(space, name="y")
    if complex_mode:
        interpolate_expression(
            y,
            cos(pi * X[0]) * cos(2.0 * pi * X[1])
            + 1.0j * cos(3.0 * pi * X[0]) * cos(4.0 * pi * X[1]))
    else:
        interpolate_expression(
            y,
            cos(pi * X[0]) * cos(2.0 * pi * X[1]))

    start_manager()
    J = forward(y)
    stop_manager()

    J_val = J.value()

    dJ = compute_gradient(J, y)

    min_order = taylor_test(forward, y, J_val=J_val, dJ=dJ)
    assert min_order > 1.99

    ddJ = Hessian(forward)
    min_order = taylor_test(forward, y, J_val=J_val, ddJ=ddJ)
    assert min_order > 2.99

    min_order = taylor_test_tlm(forward, y, tlm_order=1)
    assert min_order > 1.99

    min_order = taylor_test_tlm_adjoint(forward, y, adjoint_order=1)
    assert min_order > 1.99

    min_order = taylor_test_tlm_adjoint(forward, y, adjoint_order=2)
    assert min_order > 1.99

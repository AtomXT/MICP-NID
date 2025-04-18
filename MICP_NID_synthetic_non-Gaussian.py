'''
Date: 2024/06/11
Author: Tong Xu
This file is for running test on non-Gaussian errors.
Mixed integer convex programming with perspective strengthening + outer approximation.
'''

# import packages
import gurobipy as gp
from gurobipy import GRB
import timeit
import networkx as nx
import random
import numpy as np
import pandas as pd
import os

import causaldag as cd
import scipy
import cvxpy as cp

from functions import *


def read_non_Gaussian(m, n, alpha, k):
    """
    Read data for the test on changing level of variance difference.
    :param m:
    :param n:
    :param alpha:
    :param k: number of iteration
    :return:
    """
    file_path = './Data/SyntheticDataNID_30/'
    file_name = "non-Gaussian/data_m_{}_n_{}_alpha_{}_iter_{}.csv".format(m, n, alpha, k)
    data = pd.read_csv(file_path + file_name, header=None)
    True_B = pd.read_table(file_path + "DAG_{}.txt".format(m), delimiter=" ", header=None)
    moral = pd.read_table(file_path + "Moral_DAG_{}.txt".format(m), delimiter=" ", header=None)
    return data, True_B, moral


def optimization(input):
    M = input[0]
    N = input[1]
    alpha = input[2]
    kk = input[3]

    est = 'true'
    mgest = None  # only use true moral graph for this test

    data, True_B, moral = read_non_Gaussian(M, N, alpha, kk)
    n, p = data.shape
    l = 4*np.log(p) / n  # sparsity penalty parameter
    True_B_mat = ind2mat(True_B.values, p)
    E = [(i, j) for i in range(p) for j in range(p) if i != j]  # off diagonal edge sets
    list_edges = []
    if est == 'true':
        for edge in moral.values:
            list_edges.append((edge[0]-1, edge[1]-1))
            list_edges.append((edge[1]-1, edge[0]-1))
    else:
        for edge in mat2ind(mgest, p):
            list_edges.append((edge[0], edge[1]))
            list_edges.append((edge[1], edge[0]))

    ## Moral Graph
    G_moral = nx.Graph()
    for i in range(p):
        G_moral.add_node(i)
    G_moral.add_edges_from(list_edges)

    non_edges = list(set(E) - set(list_edges))
    # Sigma_hat = data.values.T @ data.values / n
    Sigma_hat = np.cov(data.values.T)

    ############################## Find Delta and Mu ########################################
    #########################################################################################

    # Find the smallest possible \mu such that Sigma_hat + \mu I be PD and stable.
    min_eig = np.min(scipy.linalg.eigh(Sigma_hat, eigvals_only=True))
    if min_eig < 0:
        pmu = np.abs(min_eig)  # due to numerical instability. This is the minimum value for \mu.
    else:
        pmu = 0

    # Find delta using SDP
    Lam = cp.Variable(p)
    # The operator >> denotes matrix inequality.

    constraints = [Sigma_hat + pmu * np.identity(p) - cp.diag(Lam) >> 0] + [Lam[i] >= 0 for i in range(p)]

    prob = cp.Problem(cp.Maximize(cp.sum(Lam)), constraints)
    prob.solve(solver=cp.CVXOPT)

    # Print results
    Delta = Lam.value
    Delta[Delta < 0] = 0  # Due to possible numerical instability

    ##########################################################################################
    ################################# PARAMETERS OF THE MODEL ################################
    ##########################################################################################

    m = gp.Model()
    # Create variables
    # Continuous variables

    S_var = {}
    for j, k in list_edges:
        S_var[j, k] = m.addVar(vtype=GRB.CONTINUOUS, name="s_%s_%s" % (j, k))
    for i in range(p):
        S_var[i, i] = m.addVar(vtype=GRB.CONTINUOUS, name="s_%s_%s" % (i, i))
    Gamma = {}
    for i in range(p):
        for j in range(p):
            if i == j:
                Gamma[i, j] = m.addVar(lb=1e-5, vtype=GRB.CONTINUOUS, name="Gamma%s%s" % (i, j))
            else:
                Gamma[i, j] = m.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, vtype=GRB.CONTINUOUS, name="Gamma%s%s" % (i, j))


    # Gamma = m.addMVar((p, p), ub=M, vtype=GRB.CONTINUOUS, name="Gamma")
    psi = m.addMVar((p, 1), lb=1, ub=p, vtype=GRB.CONTINUOUS, name='psi')

    # Integer variables
    # g = m.addMVar((p, p), vtype=GRB.BINARY, name='g')
    g = {}
    for i in range(p):
        for j in range(p):
            g[i, j] = m.addVar(vtype=GRB.BINARY, name="g%s%s" % (i, j))
    # z = m.addMVar((p, p), vtype=GRB.BINARY, name='z')

    # Variables for outer approximation
    T = {}
    for i in range(p):
        T[i] = m.addVar(lb=-10, ub=100, vtype=GRB.CONTINUOUS, name="T%s" % i)
        # This gives Gamma[i,i] a range about [0.0001, 100]

    m._T = T
    m._Gamma = Gamma
    m._g = g

    # define the callback function
    def logarithmic_callback(model, where):
        if where == gp.GRB.Callback.MIPSOL:
            # Get the value of Gamma
            Gamma_val = model.cbGetSolution(model._Gamma)
            for i in range(p):
                model.cbLazy(model._T[i] >= -2 * np.log(Gamma_val[i, i]) - 2 / Gamma_val[i, i] * (
                        model._Gamma[i, i] - Gamma_val[i, i]))

    Q = Sigma_hat - np.diag(Delta) + pmu * np.identity(p)
    min_eig = np.min(scipy.linalg.eigh(Q, eigvals_only=True))
    if min_eig <= 0:
        epsilon = np.abs(min_eig) + 0.0000001  # due to numerical instability. We epsilon = min_eig + 0.0000001
    else:
        epsilon = 0

    D = np.diag(Delta) - pmu * np.identity(p) - epsilon * np.identity(p)
    Q = Sigma_hat - D
    # q = np.linalg.cholesky(Q)
    # q = q.T


    # Set objective

    # log_term = gp.LinExpr()

    log_term = 0
    for i in range(p):
        log_term += T[i]

    trace = gp.QuadExpr()
    for k in range(p):
        for j in range(p):
            for i in range(p):
                trace += Gamma[i, k]*Gamma[i, j]*Q[j, k]

    perspective_bigM = gp.LinExpr()
    perspective = gp.LinExpr()
    for j in range(p):
        for i in range(p):
            perspective_bigM += Gamma[i, j]*Gamma[i, j]*D[j, j]
    # for j in range(p):
    #     perspective_bigM += D[j, j]*gp.quicksum(Gamma[k, j]*Gamma[k, j] for k in G_moral.neighbors(j))
    #     perspective_bigM += D[j, j]*Gamma[j, j]*Gamma[j, j]
    for j in range(p):
        perspective += D[j, j]*gp.quicksum(S_var[k, j] for k in G_moral.neighbors(j))
        perspective += D[j, j]*S_var[j, j]

    penalty = gp.LinExpr()
    for i, j in E:
        penalty += l*g[i, j]

    m.setObjective(log_term + trace + perspective_bigM + penalty, GRB.MINIMIZE)
    # m.setObjective(log_term + trace + penalty, GRB.MINIMIZE)

    # solve the problem without constraints to get big_M
    m.Params.lazyConstraints = 1
    m.Params.OutputFlag = 0
    m.optimize(logarithmic_callback)
    Big_M_obj = m.ObjVal
    m.update()

    big_M = 0
    for j, k in list_edges:
        big_M = max(big_M, abs(Gamma[j, k].x))

    M = 2*big_M
    # M = 2

    m.setObjective(log_term + trace + perspective + penalty, GRB.MINIMIZE)

    # log
    # t = m.addMVar((p, 1), vtype='c', lb=-gp.GRB.INFINITY, name='t')

    m.addConstrs(Gamma[i, i] <= M for i in range(p))
    m.addConstrs(Gamma[j, k] <= M*g[j, k] for j, k in list_edges)
    m.addConstrs(Gamma[j, k] >= -M*g[j, k] for j, k in list_edges)
    m.addConstrs(1-p+p*g[j, k] <= psi[k] - psi[j] for j, k in list_edges)


    # # If we use OA, we do not need this general constraint.
    # for i in range(p):
    #     m.addGenConstrLog(Gamma[i, i], t[i])
    # m.Params.FuncPieces = -1

    # if err == 'equal':
    # m.addConstrs(Gamma[j, j] == 1 for j in range(p))  ############################### Just for test!!!!!!!!!!!!

    m.addConstrs(Gamma[j, k] == 0 for j, k in non_edges)  # Use moral structure
    m.addConstrs(g[j, k] == 0 for j, k in non_edges)  # Use moral structure
    m.update()

    # Conic constraints
    for k, j in list_edges:
        m.addConstr(S_var[k, j]*g[k, j] >= Gamma[k, j]*Gamma[k, j])
        m.addConstr(S_var[k, j] <= M*M*g[k, j])
    for i in range(p):
        m.addConstr(S_var[i, i] >= Gamma[i, i]*Gamma[i, i])
        m.addConstr(S_var[i, i] <= M * M)




    # Solve
    m.Params.TimeLimit = 50*p
    # m.Params.MIPGapAbs = p * p / n
    m.Params.lazyConstraints = 1
    m.Params.OutputFlag = 1
    start = timeit.default_timer()
    m.optimize(logarithmic_callback)
    end = timeit.default_timer()

    # Extract solutions
    Gamma_ij = [var.X for var in m.getVars() if "Gamma" in var.VarName]
    t_ij = np.array([var.X for var in m.getVars() if "t" in var.VarName])
    g_ij = np.array([var.X for var in m.getVars() if "g" in var.VarName])
    # z_ij = np.array([var.X for var in m.getVars() if "z" in var.VarName])
    Gamma_opt = np.reshape(Gamma_ij, (p, p))
    g_opt = np.reshape(g_ij, (p, p))
    # z_opt = np.reshape(z_ij, (p, p))
    # t_opt = np.reshape(t_ij, (p, 1))

    D_half = np.diag(np.diag(Gamma_opt))
    B = np.eye(p) - np.linalg.inv(D_half)@Gamma_opt.T
    # np.savetxt('.\MISOCP_results\MISOCP_%s_%s_%s.csv' % (dataset, est, np.round(l, 3)), B, fmt="%.18f", delimiter=",")
    B_arcs = [[0 if np.abs(B[i][j]) <= 1e-6 or i == j else 1 for j in range(p)] for i in range(p)]

    # create a dag for computing shd for cpdag
    true_dag = cd.DAG.from_amat(np.array(True_B_mat))
    true_cpdag = true_dag.cpdag().to_amat()
    estimated_dag = cd.DAG.from_amat(np.array(B_arcs))
    estimated_cpdag = estimated_dag.cpdag().to_amat()
    SHD_cpdag = np.sum(np.abs(estimated_cpdag[0] - true_cpdag[0]))

    # SHD = compute_SHD(B_arcs, True_B_mat)
    skeleton_estimated, skeleton_true = skeleton(B_arcs), skeleton(True_B_mat)
    SHDs = compute_SHD(skeleton_estimated, skeleton_true, True)
    TPR, FPR = performance(skeleton_estimated, skeleton_true)
    run_time = end - start
    RGAP = m.MIPGAP
    return run_time, RGAP, SHD_cpdag, SHDs, TPR, FPR


if __name__ == '__main__':
    # print(optimization([10, 100, 4, 1]))

    results = []
    for mm in [10, 15, 20]:
        for nn in [400]:
            for Alpha in [4]:
                # for kk in [10]:
                for kk in range(1,31):
                    Input = [mm, nn, Alpha, kk]
                    result = optimization(Input)
                    result_i = Input + list(result)
                    print(result_i)
                    results.append(result_i)
    names = ["m", "n", "alpha", "k", "Time", "RGAP", "d_cpdag", "SHDs", "TPR", "FPR"]
    results_df = pd.DataFrame(results, columns=names)
    print(results_df)

    results_df.to_csv("./experiment results/comparison with synthetic non-Gaussian errors/1-30/MICODAG_synthetic_non-Gaussian_results_4log(m)n.csv", index=False)

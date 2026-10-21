"""Grey-box (physics-informed) regressor: closed-form physics prior + ML residual."""
from __future__ import annotations

import numpy as np
from sklearn.base import BaseEstimator, RegressorMixin, clone
from sklearn.ensemble import HistGradientBoostingRegressor

PRIOR_COL = "phys_t_est"


class GreyBoxRegressor(RegressorMixin, BaseEstimator):
    """
    y_hat = physics_prior(x) + f_ML(x)

    The prior (app.physics.quick_estimate) is already a column of X. The boosted
    trees only learn the residual the simple physics misses (cell imbalance,
    transient shape, depletion spikes). Trees cannot extrapolate, but the prior
    can, so the hybrid stays accurate at the corners of the operating envelope.
    """

    def __init__(self, residual_model=None):
        self.residual_model = residual_model

    def fit(self, X, y):
        self.model_ = clone(self.residual_model or HistGradientBoostingRegressor())
        self.model_.fit(X, np.asarray(y) - X[PRIOR_COL].to_numpy())
        return self

    def predict(self, X):
        return X[PRIOR_COL].to_numpy() + self.model_.predict(X)

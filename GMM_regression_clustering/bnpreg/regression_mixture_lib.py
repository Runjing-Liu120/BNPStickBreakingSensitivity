import jax
import jax.numpy as np
import jax.scipy as sp

import bnpmodeling.modeling_lib as modeling_lib

import paragami


##########################
# Set up vb parameters
##########################

def get_vb_params_paragami_object(dim, k_approx):
    """
    Returns a paragami patterned dictionary
    that stores the variational parameters.
    Parameters
    ----------
    dim : int
        Dimension of the regression coefficients.
    k_approx : int
        Number of components in the model.
    Returns
    -------
    vb_params_dict : dictionary
        A dictionary that contains the variational parameters.
    vb_params_paragami : paragami pattern
        A paragami pattern that contains the variational parameters.
    """

    vb_params_paragami = paragami.PatternDict()
    
    # sticks
    vb_params_paragami['stick_params'] = modeling_lib.get_stick_paragami_object(k_approx)

    # centroids
    vb_params_paragami['centroids'] = \
        paragami.NumericArrayPattern(shape=(k_approx, dim))
    
    # info of data 
    vb_params_paragami['data_info'] = \
        paragami.NumericArrayPattern(shape=(k_approx,), lb = 0.)

    vb_params_dict = vb_params_paragami.random()

    return vb_params_dict, vb_params_paragami



##########################
# Set up prior parameters
##########################
def get_default_prior_params():
    """
    Returns a paragami patterned dictionary
    that stores the prior parameters.
    
    Returns
    -------
    prior_params_dict : dictionary
        A dictionary that contains the prior parameters.
    prior_params_paragami : paragami pattern
        A paragami pattern that contains the prior parameters.
    """

    prior_params_dict = dict()
    prior_params_paragami = paragami.PatternDict()

    # DP prior parameter
    prior_params_dict['dp_prior_alpha'] = np.array([3.0])
    prior_params_paragami['dp_prior_alpha'] = \
        paragami.NumericArrayPattern(shape=(1, ), lb = 0.0)

    # normal prior on the centroids
    prior_params_dict['prior_centroid_mean'] = np.array([0.0])
    prior_params_paragami['prior_centroid_mean'] = \
        paragami.NumericArrayPattern(shape=(1, ))

    prior_params_dict['prior_centroid_info'] = np.array([0.1])
    prior_params_paragami['prior_centroid_info'] = \
        paragami.NumericArrayPattern(shape=(1, ), lb = 0.0)
    
    # normal prior on shifts
    prior_params_dict['prior_shift_mean'] = np.array([0.0])
    prior_params_paragami['prior_shift_mean'] = \
        paragami.NumericArrayPattern(shape=(1, ))

    prior_params_dict['prior_shift_info'] = np.array([0.1])
    prior_params_paragami['prior_shift_info'] = \
        paragami.NumericArrayPattern(shape=(1, ), lb = 0.0)
    
    # gamma prior on data info
    prior_params_dict['prior_data_info_shape'] = np.array([2.])
    prior_params_paragami['prior_data_info_shape'] = \
        paragami.NumericArrayPattern(shape=(1, ), lb = 0.0)

    prior_params_dict['prior_data_info_scale'] = np.array([0.05])
    prior_params_paragami['prior_data_info_scale'] = \
        paragami.NumericArrayPattern(shape=(1, ), lb = 0.0)
    
    return prior_params_dict, prior_params_paragami

##########################
# entropy term
##########################
def get_shift_entropy(e_b, e_b2): 
    
    shift_var = e_b2 - e_b**2
    
    return 0.5 * np.log(shift_var)

def get_entropy(stick_means, stick_infos, e_z,
                gh_loc, gh_weights):
    
    # entropy on memberships
    z_entropy = modeling_lib.multinom_entropy(e_z)
    
    # entropy on sticks
    stick_entropy = \
        modeling_lib.get_stick_breaking_entropy(stick_means, 
                                                stick_infos,
                                                gh_loc,
                                                gh_weights)
    
    return z_entropy + stick_entropy 

##########################
# prior term 
##########################
def get_shift_prior(e_b, e_b2, prior_mean, prior_info): 
    return prior_info * (prior_mean * e_b - 0.5 * e_b2)

def get_e_log_prior(stick_means, stick_infos, 
                    data_info, centroids,
                    prior_params_dict,
                    gh_loc, gh_weights):
    
    # get expected prior term

    # dp prior
    alpha = prior_params_dict['dp_prior_alpha']

    dp_prior = \
        modeling_lib.get_e_logitnorm_dp_prior(stick_means, stick_infos,
                                              alpha, gh_loc, gh_weights)

    # centroid prior
    prior_mean = prior_params_dict['prior_centroid_mean']
    prior_info = prior_params_dict['prior_centroid_info']

    e_centroid_prior = sp.stats.norm.logpdf(centroids, 
                                            loc = prior_mean, 
                                            scale = 1 / np.sqrt(prior_info)).sum()
        
    # prior on data info 
    shape = prior_params_dict['prior_data_info_shape']
    scale = prior_params_dict['prior_data_info_scale']
    data_info_prior = sp.stats.gamma.logpdf(data_info, 
                                            shape, 
                                            scale = scale).sum()

    return dp_prior + e_centroid_prior + data_info_prior
    
    
##########################
# Likelihood term
##########################
def get_loglik_obs_by_nk(y, x, centroids, data_info, e_b, e_b2):
    # returns a N by k_approx matrix where the (n,k)th entry is the
    # expected log likelihood of the nth observation when it belongs to
    # component k.

    num_time_points = x.shape[0]

    # y is (obs x time points) = (n x t)
    # x is (time points x basis vectors) = (t x b)
    # centroids is (clusters x basis vectors) = (k x b)
    x_times_beta = np.einsum('tb,kb->tk', x, centroids)
    
    # mu_nkt = beta_k^T x_t + b_nk

    # \sum_t (y_nt - mu_nkt) ^ 2 =
    #   \sum_t (y_nt^2 - 2 * y_nt * mu_nkt + mu_nkt ^ 2) =
    #   \sum_t (linear_term +                quad_term)

    # \sum_t E(y^2 - 2 * y * mu) =
    #        E(sum(y_t^2) - 2 * sum(y) * beta_k^T x - 2 * sum(y_t) * b_nk):
    linear_term = \
        np.sum(y ** 2, axis=1, keepdims=True) + \
        -2 * np.einsum('nt,tk->nk', y, x_times_beta) + \
        -2 * np.sum(y, axis = 1, keepdims = True) * e_b

    # This is E(mu^2) =
    #         E[(beta_k^T x)^2 +
    #           2 * b_n * beta_k^T x +
    #           b_n^2]
    quad_term = np.sum(x_times_beta ** 2, axis=0, keepdims=True) + \
                2 * np.sum(x_times_beta, axis=0, keepdims=True) * e_b + \
                e_b2 * num_time_points

    square_term = -0.5 * np.expand_dims(data_info, axis = 0) * \
                    (linear_term + quad_term)

    # We have already summed the (y - mu)^2 terms over time points,so
    # we need to multiply the e_log_info_y by the number of points we've
    # summed over.
    log_info_term = \
        0.5 * np.log(data_info) * \
        num_time_points
    
    return  square_term + np.expand_dims(log_info_term, axis = 0)

def get_optimal_shifts(y, x, centroids, data_info, prior_params_dict): 
    
    num_time_points = x.shape[0]
    
    data_info = np.expand_dims(data_info, axis = 0)
    
    # y is (obs x time points) = (n x t)
    # x is (time points x basis vectors) = (t x b)
    # centroids is (clusters x basis vectors) = (k x b)
    x_times_beta = np.einsum('tb,kb->tk', x, centroids)
    
    # ydiff is (y - xbeta), but summed over time 
    ydiff = (np.expand_dims(y, axis = -1) - \
             np.expand_dims(x_times_beta, axis = 0)).sum(1) * data_info
    
    prior_diff = prior_params_dict['prior_shift_info'] * prior_params_dict['prior_shift_mean']
    
    # mean and variance of optimal shift
    e_b = (ydiff + prior_diff) / (num_time_points * data_info + prior_params_dict['prior_shift_info'])
    var_b = 1 / (num_time_points  * data_info + prior_params_dict['prior_shift_info'])
    
    # second moment
    e_b2 = var_b + e_b**2
    
    return e_b, e_b2


##########################
# Optimization over e_z
##########################
def get_z_nat_params(y, x, 
                     stick_means, stick_infos,
                     data_info, centroids, 
                     e_b, e_b2, 
                     gh_loc, gh_weights, 
                     prior_params_dict):

    # get likelihood term
    loglik_obs_by_nk = get_loglik_obs_by_nk(y, x, centroids, data_info, e_b, e_b2) 

    # get weight term    
    e_log_cluster_probs = modeling_lib.\
        get_e_log_cluster_probabilities(stick_means, stick_infos,
                                        gh_loc, gh_weights)
    
    # prior on shifts 
    prior_shift_mean = prior_params_dict['prior_shift_mean']
    prior_shift_info = prior_params_dict['prior_shift_info']
    shift_prior = get_shift_prior(e_b, e_b2, prior_shift_mean, prior_shift_info)
    
    # entropy on shifts 
    shift_entropy = get_shift_entropy(e_b, e_b2) 
    
    z_nat_param = loglik_obs_by_nk + e_log_cluster_probs + shift_prior + shift_entropy

    return z_nat_param

def get_optimal_z(y, x, 
                  stick_means, stick_infos,
                  data_info, centroids,
                  e_b, e_b2, 
                  gh_loc, gh_weights, 
                  prior_params_dict):

    z_nat_param = \
        get_z_nat_params(y, x, 
                         stick_means, stick_infos,
                         data_info, centroids,
                         e_b, e_b2, 
                         gh_loc, gh_weights, 
                         prior_params_dict)

    e_z = jax.nn.softmax(z_nat_param, axis = 1)
    
    return e_z, z_nat_param


def get_kl(y, x,
           vb_params_dict, prior_params_dict,
           gh_loc, gh_weights,
           e_z = None,
           e_log_phi = None):

    """
    Computes the negative ELBO

    Parameters
    ----------
    y : ndarray
        The array of observations shape (num_obs x n_timepoints) 
    x : ndarray 
        The b-spline regression matrix, shape = (n_timepoints, dim).
    vb_params_dict : dictionary
        Dictionary of variational parameters.
    prior_params_dict : dictionary
        Dictionary of prior parameters.
    gh_loc : vector
        Locations for gauss-hermite quadrature. We need this compute the
        expected prior terms.
    gh_weights : vector
        Weights for gauss-hermite quadrature. We need this compute the
        expected prior terms.
    e_z : ndarray (optional)
        The cluster belongings , stored in an array whose
        (n, k)th entry is the probability
        of the nth datapoint belonging to cluster k.
        If ``None``, we set the optimal z implicitly.
    e_log_phi : callable, optional
        A function that returns the (scalar) expectation of the
        perturbation `log_phi` as a function of the 
        logit-normal mean and info parameters.
        if `None`, no perturbation is considered. 

    Returns
    -------
    kl : float
        The negative elbo.
    """

    # get vb parameters
    stick_means = vb_params_dict['stick_params']['stick_means']
    stick_infos = vb_params_dict['stick_params']['stick_infos']
    centroids = vb_params_dict['centroids']
    data_info = vb_params_dict['data_info']
    
    # get optimal shifts
    e_b, e_b2 = get_optimal_shifts(y, x, centroids, data_info, prior_params_dict)
    
    # get optimal cluster belongings
    e_z_opt, z_nat_param = \
            get_optimal_z(y, x, 
                          stick_means, stick_infos,
                          data_info, centroids,
                          e_b, e_b2, 
                          gh_loc, gh_weights, 
                          prior_params_dict)
    if e_z is None:
        e_z = e_z_opt
    
    e_loglik = np.sum(e_z * z_nat_param) 

    # entropy term
    entropy = get_entropy(stick_means, stick_infos, e_z,
                          gh_loc, gh_weights)

    # prior term
    e_log_prior = get_e_log_prior(stick_means, stick_infos, 
                                    data_info, centroids,
                                    prior_params_dict,
                                    gh_loc, gh_weights)
    
    elbo = e_log_prior + entropy + e_loglik
        
    if e_log_phi is not None:

        e_log_pert = e_log_phi(vb_params_dict['stick_params']['stick_means'],
                               vb_params_dict['stick_params']['stick_infos'])
                                                            
        elbo = elbo + e_log_pert
        
    return -1 * elbo.squeeze()




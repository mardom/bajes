#!/usr/bin/env python
from __future__ import division, unicode_literals
import sys
import os
import logging

import mpi4py

mpi4py.rc.threads = False
mpi4py.rc.recv_mprobe = False

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["MKL_DYNAMIC"] = "0"

from bajes.pipe import set_logger, ensure_dir, init_sampler, parse_core_options, init_proposal
from bajes_core import print_header, init_core, finalize

# Necessary to add cwd to path when script run
# by SLURM (since it executes a copy)
sys.path.append(os.getcwd())

# wrapper functions

def _log_like_func(x):
    return post.log_like(x)

def _log_likeprior_func(x):
    return post.log_likeprior(x)

def _log_posterior_func(x):
    return post.log_post(x)

def _prior_transform_func(x):
    return post.prior_transform(x)

def _propose_func(*args, **kwargs):
    return prop.propose(*args, **kwargs)

def _hack_funcs(inf):
    
    if engine == 'mcmc':
        inf.log_prob_fn = _log_posterior_func
        inf._propose    = _propose_func
    
    elif engine == 'ptmcmc':
        inf._likeprior    = _log_likeprior_func
        inf._proposal_fn  = _propose_func

    elif engine == 'nest' or engine == 'dynest':
        # from dynesty.dynesty import _function_wrapper
        inf.sampler.prior_transform = _prior_transform_func 
        inf.sampler.loglikelihood   = _log_like_func
        inf.sampler.evolve_point    = _propose_func

    return inf

if __name__ == "__main__":
    
    global engine
    global post
    global prop

    # parse input arguments
    opts,args = parse_core_options()
    os.environ["MPI_PER_NODE"] = "{}".format(opts.mpi_per_node)
    engine = opts.engine
    tracing = opts.trace_memory
    
    # make output directory and initialize logger
    opts.outdir = os.path.abspath(opts.outdir)
    ensure_dir(opts.outdir)

    # initialize MPI and pool
    from bajes.pipe import initialize_mpi_pool
    Pool, close_pool = initialize_mpi_pool(opts.fast_mpi)

    if opts.engine == 'cpnest':
        raise AttributeError("MPI parallelisation not available with cpnest sampler.")

    if Pool.rank == 0:
        
        # start memory tracing, if requested
        if tracing:
            import tracemalloc
            tracemalloc.start(25)
        
        if opts.debug:
            logger = set_logger(outdir=opts.outdir, level='DEBUG', silence=opts.silence)
            logger.debug("Using logger with debugging mode")
        else:
            logger = set_logger(outdir=opts.outdir, silence=opts.silence)
        
        # print header
        print_header(logger, opts.tags, opts.engine, opts.nprocs, p_tag=True)
        logger.info("> MPI world initisalized")

        # initialize likelihood+prior
        opts, post  = init_core(opts)
        prop        = init_proposal(engine, post, use_slice=opts.use_slice,
                                    use_gw=opts.use_gw,
                                    maxmcmc=opts.maxmcmc, minmcmc=opts.minmcmc,
                                    nact=opts.nact)

    else:
        
        # initialize likelihood+prior in slaves
        logger = set_logger(outdir=opts.outdir, level='ERROR', silence=True)
        opts, post  = init_core(opts)
        prop        = init_proposal(engine, post, use_slice=opts.use_slice,
                                    use_gw=opts.use_gw,
                                    maxmcmc=opts.maxmcmc, minmcmc=opts.minmcmc,
                                    nact=opts.nact)

        # delete inputs
        del opts
        del args

    # starting sampling
    with Pool as pool:
        
        if not pool.is_master():
            pool.wait()
            sys.exit(0)

        # initialize sampler
        inference = init_sampler(post, pool, opts, prop)

        # hack inference
        inference = _hack_funcs(inference)

        # running sampler
        logger.info("Running sampling ...")
        inference.run()

    # produce posteriors
    finalize(logger, inference)

    # stop memory tracing, if needed
    if tracing:
        tracemalloc.stop()

    # close parallel pool
    close_pool(Pool)

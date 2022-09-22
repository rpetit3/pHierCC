#!/usr/bin/env python

import os, sys, pandas as pd, numpy as np, logging
import click
from sklearn.metrics import silhouette_score, normalized_mutual_info_score
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.colors as colors
from multiprocessing import Pool
import SharedArray as sa
from tempfile import NamedTemporaryFile

try :
    from getDistance import getDistance
except :
    from .getDistance import getDistance

logging.basicConfig(format='%(asctime)s | %(message)s',stream=sys.stdout, level=logging.INFO)


def get_similarity2(data) :
    method, cc1, cc2 = data
    if np.unique(cc1).size == 1 and  np.unique(cc1).size == 1 :
        return 1.
    return method(cc1, cc2)

def get_similarity(method, cluster, stepwise, pool) :
    logging.info('Calculating NMIs...')
    similarity = np.ones([cluster.shape[1], cluster.shape[1]], dtype=np.float64)
    for i1, cc1 in enumerate(cluster.T) :
        if i1 % 10 == 0 :
            logging.info('    NMIs between level {0} and greater levels'.format(i1 * stepwise))
        similarity[i1, i1+1:] = pool.map(get_similarity2, [ [method, cc1, cc2] for cc2 in cluster.T[i1+1:] ])
        similarity[i1+1:, i1] = similarity[i1, i1+1:]
    similarity[similarity>0.999] = 0.999
    similarity[similarity<0.0] = 0.0
    return similarity

def get_silhouette(profile, cluster, stepwise, pool) :
    logging.info('Calculating pairwise distance ...')
    dist = getDistance(profile, 'p_dist', pool)
    with NamedTemporaryFile(prefix='HCCeval_') as file :
        dist_buf = 'file://{0}.dist'.format(file.name)
        dist2 = sa.create(dist_buf, dist.shape[:2], dist.dtype)
        dist2[:] = dist[:, :, 0] + dist[:, :, 0].T
        del dist
        logging.info('Calculating Silhouette score ...')
        silhouette = np.array(pool.map(get_silhouette2, [ [dist_buf, tag] for tag in cluster.T ]))
        sa.delete(dist_buf)
    return silhouette

def get_silhouette2(data) :
    dist_buf, tag = data
    s = np.unique(tag)
    if 2 <= s.size < tag.shape[0] :
        dist = sa.attach(dist_buf)
        ss = silhouette_score(dist.astype(float), tag, metric = 'precomputed')
        return ss
    else :
        return 0.

def prepare_mat(profile_file) :
    mat = pd.read_csv(profile_file, sep='\t', header=None, dtype=str).values
    allele_columns = np.array([i == 0 or (not h.startswith('#')) for i, h in enumerate(mat[0])])
    mat = mat[1:, allele_columns]
    return mat


@click.command()
@click.option('-p', '--profile', help='[INPUT] Name of a profile file consisting of a table of columns of the ST numbers and the allelic numbers, separated by tabs. Can be GZIPped.', required=True)
@click.option('-c', '--cluster', help='[INPUT] Name of the pHierCC text output. Can be GZIPped.', required=True)
@click.option('-o', '--output', help='[OUTPUT] Prefix for the two output files.', required=True)
@click.option('-s', '--stepwise', help='[INPUT; optional] Evaluate every <stepwise> levels (Default: 10).', default=10, type=int)
@click.option('-n', '--n_proc', help='[INPUT; optional] Number of processes (CPUs) to use (Default: 4).', default=4, type=int)
def evalHCC(profile, cluster, output, stepwise, n_proc) :
    '''evalHCC evaluates a HierCC scheme using varied statistic summaries.'''
    pool = Pool(n_proc)

    profile = prepare_mat(profile)
    cluster = prepare_mat(cluster)

    idx = { p:i for i, p in enumerate(profile.T[0])}
    cluster_idx = sorted([ [idx.get(c, -1), i] for i, c in enumerate(cluster.T[0]) if c in idx ])
    cluster = cluster[np.array(cluster_idx).T[1]]

    idx = { p:i for i, p in enumerate(cluster.T[0])}
    cluster_idx = sorted([ [idx.get(c, -1), i] for i, c in enumerate(profile.T[0]) if c in idx ])
    profile = profile[np.array(cluster_idx).T[1]]

    cluster.T[0] = np.arange(cluster.shape[0])
    profile.T[0] = np.arange(profile.shape[0])
    cluster = cluster.astype(int)
    profile = profile.astype(int)
    cluster[cluster < 0] = 0
    profile[profile < 0] = 0

    cluster = cluster[:, 1::stepwise]

    silhouette = get_silhouette(profile, cluster, stepwise, pool)
    similarity = get_similarity(normalized_mutual_info_score, cluster, stepwise, pool)

    with open(output+'.tsv', 'w') as fout:
        levels = ['HC{0}'.format(lvl*stepwise) for lvl in np.arange(silhouette.shape[0])]
        for lvl, ss in zip(levels, silhouette) :
            fout.write('#Silhouette\t{0}\t{1}\n'.format(lvl, ss))

        fout.write('\n#NMI\t{0}\n'.format('\t'.join(levels)))
        for lvl, nmis in zip(levels, similarity):
            fout.write('{0}\t{1}\n'.format(lvl, '\t'.join([ '{0:.3f}'.format(nmi) for nmi in nmis ])))
    fig, axs = plt.subplots(2, 2, \
                            figsize=(8, 12), \
                            gridspec_kw={'width_ratios':(12, 1),
                                         'height_ratios': (65, 35)})

    heatplot = axs[0, 0].imshow( (10*(np.log10(1-similarity))), \
                                norm=colors.TwoSlopeNorm(vmin=-30., vcenter=-10., vmax=0), \
                                cmap = 'RdBu',\
                                extent=[0, silhouette.shape[0]*stepwise, \
                                        silhouette.shape[0]*stepwise, 0])
    cb = fig.colorbar(heatplot, cax=axs[0, 1])
    axs[1, 0].plot(np.arange(silhouette.shape[0])*stepwise, silhouette,)
    axs[1, 0].set_xlim([0, silhouette.shape[0]*stepwise])
    axs[1, 1].remove()
    axs[0, 0].set_ylabel('HCs (allelic distances)')
    axs[0, 0].set_xlabel('HCs (allelic distances)')
    axs[1, 0].set_ylabel('Silhouette scores')
    axs[1, 0].set_xlabel('HCs (allelic distances)')
    cb.set_label('Normalized Mutual Information')
    cb.set_ticks([-30, -23.01, -20, -13.01, -10, -3.01, 0])
    cb.ax.set_yticklabels(['>=.999', '.995', '.99', '.95', '.9', '.5', '.0'])
    plt.savefig(output+'.pdf')
    logging.info('Tab-delimited evaluation is saved in {0}.tsv'.format(output))
    logging.info('Visualisation is saved in {0}.pdf'.format(output))
    pool.close()

if __name__ == '__main__' :
    evalHCC()

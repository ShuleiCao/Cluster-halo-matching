import h5py
from astropy.io import fits
import numpy as np
import healpy as hp
import os
from astropy.table import Table, vstack
from joblib import Parallel, delayed
from collections import defaultdict
from scipy.spatial import cKDTree
from joblib import Parallel, delayed
import gc
from tqdm import tqdm

import logging

# Set up logging configuration
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


def get_name(file_path, names, mask=None, file_format='fits', key='gold'):
    if file_format == 'fits':
        with fits.open(file_path) as hdul:
            data = hdul[1].data  
            data_names = {name: data[name][mask] if mask is not None else data[name] for name in names}
    elif file_format == 'hdf5':
        with h5py.File(file_path, 'r') as f:
            data_names = {name: f['catalog/'][key][name][:][mask] if mask is not None else f['catalog/'][key][name][:] for name in names}
    else:
        raise ValueError(f"Unknown file format: {file_format}")
    return data_names

# Function to compute pixel ID from RA, Dec
def compute_pixel_id(ra, dec, nside=8):
    theta = 0.5 * np.pi - np.deg2rad(dec)
    phi = np.deg2rad(ra)
    return hp.ang2pix(nside, theta, phi)

# Load specific columns from a dataset by pixel ID
def load_data_for_pixel(file_path, pixel_id, columns, pixel_ids_all, file_format='fits', key=None):
    relevant_mask = pixel_ids_all == pixel_id
    try:
        if file_format == 'hdf5':
            with h5py.File(file_path, 'r') as f:
                return {col: f['catalog/'][key][col][:][relevant_mask] for col in columns}
        elif file_format == 'fits':
            with fits.open(file_path) as hdul:
                data_table = hdul[1].data
                data = {col: data_table[col][relevant_mask] for col in columns}
                return data
    except TypeError:
        # Debugging information
        print(f"File Path: {file_path}")
        print(f"Pixel ID: {pixel_id}")
        print(f"Columns: {columns}")
        print(f"File Format: {file_format}")
        print(f"Key: {key}")
        raise

def get_relevant_neighboring_pixels(pixel_id, gold_pixel_ids, nside=8):
    """Return a list of neighboring pixel IDs for a given pixel from the gold dataset."""
    theta, phi = hp.pix2ang(nside, pixel_id)
    neighbors = hp.get_all_neighbours(nside, theta, phi)
    neighbors = neighbors[~np.isnan(neighbors)].astype(int)
    return [pid for pid in neighbors if pid in gold_pixel_ids]

def load_data_for_pixel_and_neighbors(pixel_id, gold_data_path, columns, gold_pixel_ids, file_format='hdf5', key='gold'):
    # Start with the main pixel's data
    data = load_data_for_pixel(gold_data_path, pixel_id, columns, gold_pixel_ids, file_format, key)
    
    # Get the relevant neighboring pixels for the given pixel from the gold dataset
    pixels_to_load = get_relevant_neighboring_pixels(pixel_id, gold_pixel_ids)
    
    # Ensure consistent order for processing neighboring pixels
    pixels_to_load.sort()
    
    for pid in pixels_to_load:
        pixel_data = load_data_for_pixel(gold_data_path, pid, columns, gold_pixel_ids, file_format, key)
        for col in columns:
            data[col] = np.concatenate([data[col], pixel_data[col]])
    return data

base_path = '/lustre/work/client/users/shuleic/Cardinalv3/'
gold_path = os.path.join(base_path, 'Cardinal-3_v2.0_Y6a_gold.h5')
bpz_path = os.path.join(base_path, 'Cardinal-3_v2.0_Y6a_bpz.h5')

# Compute pixel IDs
gold_radec = get_name(gold_path, ['ra', 'dec'], file_format='hdf5', key='gold')
gold_pixels = compute_pixel_id(gold_radec['ra'], gold_radec['dec'])
del gold_radec

np.savez(os.path.join(base_path, 'gold_pixels_nside8.npz'), gold_pixels=gold_pixels)
# gold_pixels = np.load(os.path.join(base_path, 'gold_pixels_nside8.npz'))['gold_pixels']

unique_gold_pixels = np.unique(gold_pixels)

# Initialize data containers
halo_data = []

# Process each pixel ID
def process_halo_pixel(pixel_id):
    gold_data_pixel = load_data_for_pixel(gold_path, pixel_id, ['ra', 'dec', 'coadd_object_id', 'haloid', 'rhalo', 'r200', 'm200', 'px', 'py', 'pz'], gold_pixels, file_format='hdf5', key='gold')
    z_values = load_data_for_pixel(bpz_path, pixel_id, ['redshift_cos'], gold_pixels, file_format='hdf5', key='bpz')
    gold_data_pixel['z'] = z_values['redshift_cos']
    del z_values
    gc.collect()

    # Filter halo data where m200 > 0 and rhalo = 0
    halo_mask = (gold_data_pixel['rhalo'] == 0) & (gold_data_pixel['m200'] > 0)
    halo_data_pixel_table = Table({col: list(gold_data_pixel[col][halo_mask]) for col in gold_data_pixel.keys()})

    del halo_mask, gold_data_pixel
    gc.collect()
    
    return halo_data_pixel_table

results = Parallel(n_jobs=-1, verbose=10)(delayed(process_halo_pixel)(pixel_id) for pixel_id in tqdm(unique_gold_pixels, desc="Processing pixels"))

halo_data = list(results)

# Convert to single Tables
halo_data = vstack(halo_data)
halo_data.write(os.path.join(base_path,'halo_data_all.fits'), overwrite=True)
# del halo_data
# gc.collect()

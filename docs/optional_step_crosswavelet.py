#!/usr/bin/env python3
"""
optional_step_crosswavelet.py https://pycwt.readthedocs.io/en/latest/tutorial/cwt/

Usage:
    python optional_step_crosswavelet.py --config config.json --output-dir assets/rqa
"""
import numpy as np
import pandas as pd
import pycwt as wavelet
from pycwt.helpers import find
import json
import os
import argparse
from scipy import signal
import warnings
warnings.filterwarnings('ignore')

# ==============================================================================
# CONFIGURABLE PARAMETERS - Adjust these as needed
# ==============================================================================

# ---------- Wavelet Transform Parameters ----------
MOTHER_WAVELET = 'morlet'  # Options: 'morlet', 'paul', 'dog', 'mexican_hat'
OMEGA0 = 6                  # Parameter for Morlet wavelet (typically 6)
DJ = 1/12                   # Frequency resolution (1/12 = 12 sub-octaves per octave)
S0_FACTOR = 2               # Smallest scale as multiple of dt (s0 = S0_FACTOR * dt)
J_AUTO = True               # Auto-calculate number of scales based on data length
J_MANUAL = 7                # Manual number of octaves (used if J_AUTO = False)

# ---------- Data Processing Parameters ----------
DETREND_DATA = True         # Whether to detrend the data (polynomial fit removal)
DETREND_ORDER = 1           # Polynomial order for detrending (1 = linear)
MIN_DATA_POINTS = 50        # Minimum number of data points required
INTERPOLATION_METHOD = 'linear'  # Method for interpolating to common time base

# ---------- Significance Testing Parameters ----------
SIGNIFICANCE_LEVEL = 0.95   # Confidence level for significance testing (0.95 = 95%)
USE_AR1_NOISE = True        # Use AR1 noise model for significance (vs white noise)
MONTE_CARLO_ITERATIONS = 0  # Number of Monte Carlo iterations (0 = use theoretical)

# ---------- Scale-Averaged Band Parameters ----------
SCALE_AVG_BAND_AUTO = True  # Auto-calculate scale-averaging band
SCALE_AVG_MIN_PERIOD = 2.0  # Minimum period for scale-averaging (in time units)
SCALE_AVG_MAX_PERIOD = 8.0  # Maximum period for scale-averaging (in time units)
# Note: If auto, uses 2*dt to min(8*dt, max_period/2)

# ---------- Coherence Calculation Parameters ----------
COHERENCE_SMOOTH_TIME = True    # Apply temporal smoothing for coherence
COHERENCE_SMOOTH_SCALE = True   # Apply scale smoothing for coherence
COHERENCE_TIME_FACTOR = 1.0     # Time smoothing factor (higher = more smoothing)
COHERENCE_SCALE_WIDTH = 0.6      # Scale smoothing width in DJ units

# ---------- Visualization/Storage Parameters ----------
MAX_TIME_POINTS_VIZ = 500    # Maximum time points for visualization (downsampling)
MAX_FREQ_POINTS_VIZ = 100    # Maximum frequency points for visualization
SAVE_FULL_RESOLUTION = False # Also save full resolution data (warning: large files)
OUTPUT_FORMAT = 'json'       # Output format: 'json', 'npz', or 'both'

# ---------- Analysis Coverage Parameters ----------
COMPUTE_ALL_PAIRS = True        # Compute all possible pairs (not just specified)
SYMMETRIC_PAIRS = False          # Compute both A vs B and B vs A (usually redundant)

# ---------- Quality Control Parameters ----------
HIGH_COHERENCE_THRESHOLD = 0.8  # Threshold for "high coherence" statistics
COI_EXCLUDE = True               # Exclude COI regions from statistics
MIN_COMMON_TIME_FRACTION = 0.5  # Minimum overlap fraction required for pairs
EDGE_TAPER = True                # Apply edge tapering (Tukey window)
TAPER_ALPHA = 0.05               # Tukey window parameter (0-1, smaller = less taper)

# ---------- File Path Parameters ----------
INPUT_DIR = 'assets/timeseries'     # Directory containing input CSV files
OUTPUT_DIR = 'assets/crosswavelet'  # Default output directory
FILE_PATTERN = '{video_id}_{data_type}.csv'  # Input file naming pattern

# ---------- Debugging/Logging Parameters ----------
VERBOSE = True              # Print detailed progress information
DEBUG_MODE = False          # Save intermediate results for debugging
PLOT_INPUTS = False         # Plot input time series before processing
SAVE_METADATA = True        # Save analysis metadata in output

# ==============================================================================
# END OF CONFIGURABLE PARAMETERS
# ==============================================================================

def load_and_prepare_timeseries(video_id, data_type, detrend=DETREND_DATA):
    """
    Load and prepare time series data for cross-wavelet analysis.
    Following Torrence and Compo (1998) approach for data preparation.
    
    Parameters:
    - video_id: Video identifier
    - data_type: Type of data (e.g., 'bodysync', 'neuralsync')
    - detrend: Whether to detrend the data
    """
    csv_path = os.path.join(INPUT_DIR, FILE_PATTERN.format(
        video_id=video_id, data_type=data_type
    ))
    
    if not os.path.exists(csv_path):
        if VERBOSE:
            print(f"Warning: File not found: {csv_path}")
        return None, None, None, None, None
    
    # Load data
    df = pd.read_csv(csv_path)
    
    # Get time column
    if 'Time' not in df.columns:
        if VERBOSE:
            print(f"Error: No 'Time' column in {csv_path}")
        return None, None, None, None, None
    
    # Get all non-time columns
    data_cols = [col for col in df.columns if col != 'Time']
    if not data_cols:
        if VERBOSE:
            print(f"Error: No data columns in {csv_path}")
        return None, None, None, None, None
    
    # Use the first data column
    data_col = data_cols[0]
    
    # Clean data - remove NaN values
    mask = ~pd.isna(df[data_col])
    time_clean = df['Time'][mask].values
    data_clean = df[data_col][mask].values
    
    if len(data_clean) < MIN_DATA_POINTS:
        if VERBOSE:
            print(f"  Insufficient data points ({len(data_clean)}) < {MIN_DATA_POINTS}")
        return None, None, None, None, None
    
    # Calculate sampling interval
    dt = np.median(np.diff(time_clean))
    
    # Detrend if requested
    if detrend and DETREND_ORDER > 0:
        p = np.polyfit(time_clean - time_clean[0], data_clean, DETREND_ORDER)
        data_detrended = data_clean - np.polyval(p, time_clean - time_clean[0])
        if DEBUG_MODE and VERBOSE:
            print(f"  Detrended with polynomial order {DETREND_ORDER}")
    else:
        data_detrended = data_clean - np.mean(data_clean)
    
    # Calculate statistics
    std = data_detrended.std()
    var = std ** 2
    
    # Normalize by standard deviation
    data_normalized = data_detrended / std
    
    if VERBOSE:
        print(f"  Loaded {data_type}: {len(data_clean)} points, dt={dt:.4f}, std={std:.4f}")
    
    return data_normalized, time_clean, dt, std, var

def compute_cross_wavelet_standard(data1, data2, time, dt, 
                                   mother=MOTHER_WAVELET, omega0=OMEGA0,
                                   dj=DJ, s0=None, J=None):
    """
    Compute cross-wavelet transform between two time series using pycwt standard approach.
    
    Parameters:
    - data1, data2: Input time series (normalized)
    - time: Time array
    - dt: Sampling interval
    - mother: Mother wavelet name
    - omega0: Omega0 for Morlet wavelet
    - dj: Frequency resolution parameter
    - s0: Smallest scale
    - J: Number of scales
    """
    
    N = len(data1)
    
    # Set default parameters following Torrence & Compo
    if s0 is None:
        s0 = S0_FACTOR * dt  # Starting scale
    
    if J is None:
        if J_AUTO:
            J = np.log2(N * dt / s0) / dj  # Auto-calculate number of scales
        else:
            J = J_MANUAL / dj  # Use manual setting
    
    # Select mother wavelet
    if mother.lower() == 'morlet':
        mother_wavelet = wavelet.Morlet(omega0)
    elif mother.lower() == 'paul':
        mother_wavelet = wavelet.Paul()
    elif mother.lower() == 'dog':
        mother_wavelet = wavelet.DOG()
    elif mother.lower() == 'mexican_hat':
        mother_wavelet = wavelet.MexicanHat()
    else:
        if VERBOSE:
            print(f"Unknown wavelet '{mother}', using Morlet")
        mother_wavelet = wavelet.Morlet(omega0)
    
    # Calculate lag-1 autocorrelation for AR1 noise model
    if USE_AR1_NOISE:
        alpha1, _, _ = wavelet.ar1(data1)
        alpha2, _, _ = wavelet.ar1(data2)
    else:
        alpha1 = alpha2 = 0.0  # White noise
    
    if DEBUG_MODE and VERBOSE:
        print(f"  AR1 coefficients: α1={alpha1:.3f}, α2={alpha2:.3f}")
    
    # Perform continuous wavelet transform for both series
    W1, scales, freqs, coi, fft1, fftfreqs = wavelet.cwt(
        data1, dt, dj, s0, J, mother_wavelet
    )
    
    W2, _, _, _, fft2, _ = wavelet.cwt(
        data2, dt, dj, s0, J, mother_wavelet
    )
    
    # Calculate cross-wavelet transform
    XWT = W1 * np.conj(W2)
    
    # Calculate cross-wavelet power
    power = np.abs(XWT)
    
    # Calculate phase difference
    phase = np.angle(XWT)
    
    # Convert frequencies to periods
    period = 1 / freqs
    
    # Calculate significance for XWT
    signif_xwt, _ = wavelet.significance(
        1.0, dt, scales, 0, np.mean([alpha1, alpha2]),
        significance_level=SIGNIFICANCE_LEVEL, wavelet=mother_wavelet
    )
    sig95_xwt = np.ones([1, N]) * signif_xwt[:, None]
    sig95_xwt = power / sig95_xwt
    
    # Calculate wavelet coherence (requires smoothing)
    def smooth_wavelet(W, scales, dt, dj, mother):
        """Smooth wavelet spectrum in both time and scale."""
        # Smooth in time
        smooth_time = np.zeros_like(W)
        n_time = W.shape[1]
        
        for i, scale in enumerate(scales):
            # Smoothing window size proportional to scale
            if COHERENCE_SMOOTH_TIME:
                window_size = int(scale / dt * COHERENCE_TIME_FACTOR)
                if window_size < 3:
                    window_size = 3
                if window_size % 2 == 0:
                    window_size += 1
                
                # Apply smoothing - ensure output matches expected length
                kernel = np.ones(window_size) / window_size
                convolved = np.convolve(np.abs(W[i, :])**2, kernel, mode='same')
                
                # Handle any size mismatches from convolution
                if len(convolved) != n_time:
                    if len(convolved) > n_time:
                        # Truncate to match expected size
                        convolved = convolved[:n_time]
                    else:
                        # Pad to match expected size (edge mode preserves boundaries)
                        pad_width = n_time - len(convolved)
                        convolved = np.pad(convolved, (0, pad_width), mode='edge')
                
                smooth_time[i, :] = convolved
            else:
                smooth_time[i, :] = np.abs(W[i, :])**2
        
        # Smooth in scale
        if COHERENCE_SMOOTH_SCALE:
            smooth_scale = np.zeros_like(smooth_time)
            n_scales = smooth_time.shape[0]
            scale_window = int(COHERENCE_SCALE_WIDTH / dj)
            if scale_window < 1:
                scale_window = 1
            
            for j in range(n_time):
                kernel = np.ones(scale_window) / scale_window
                convolved = np.convolve(smooth_time[:, j], kernel, mode='same')
                
                # Handle any size mismatches
                if len(convolved) != n_scales:
                    if len(convolved) > n_scales:
                        convolved = convolved[:n_scales]
                    else:
                        pad_width = n_scales - len(convolved)
                        convolved = np.pad(convolved, (0, pad_width), mode='edge')
                
                smooth_scale[:, j] = convolved
        else:
            smooth_scale = smooth_time
        
        return smooth_scale
    
    # Calculate smoothed spectra for coherence
    S1 = smooth_wavelet(W1, scales, dt, dj, mother_wavelet)
    S2 = smooth_wavelet(W2, scales, dt, dj, mother_wavelet)
    S12 = smooth_wavelet(XWT, scales, dt, dj, mother_wavelet)
    
    # Wavelet coherence
    WCO = np.abs(S12) / np.sqrt(S1 * S2)
    WCO = np.minimum(WCO, 1.0)  # Ensure coherence <= 1
    
    # Phase angles for plotting (only where coherence is significant)
    phase_angle = np.angle(XWT)
    
    # Global wavelet spectrum (time-averaged)
    global_power = power.mean(axis=1)
    
    # Calculate degrees of freedom for global spectrum
    dof = N - scales
    global_signif, _ = wavelet.significance(
        1.0, dt, scales, 1, np.mean([alpha1, alpha2]),
        significance_level=SIGNIFICANCE_LEVEL, dof=dof, wavelet=mother_wavelet
    )
    
    return {
        'W1': W1,
        'W2': W2,
        'XWT': XWT,
        'power': power,
        'phase': phase_angle,
        'coherence': WCO,
        'scales': scales,
        'freqs': freqs,
        'period': period,
        'coi': coi,
        'sig95_xwt': sig95_xwt,
        'signif_xwt': signif_xwt,
        'global_power': global_power,
        'global_signif': global_signif,
        'mother': mother_wavelet,
        'dt': dt,
        'alpha1': alpha1,
        'alpha2': alpha2,
        'dj': dj,
        's0': s0,
        'J': J
    }

def downsample_for_storage(cwt_results, time, scale_avg_power, 
                           max_time_points=MAX_TIME_POINTS_VIZ, 
                           max_freq_points=MAX_FREQ_POINTS_VIZ):
    """
    Downsample the wavelet results for efficient storage and visualization.
    """
    n_time = len(time)
    n_freq = len(cwt_results['freqs'])
    
    # Determine downsampling factors
    time_factor = max(1, n_time // max_time_points)
    freq_factor = max(1, n_freq // max_freq_points)
    
    # Downsample time
    time_ds = time[::time_factor]
    
    # Downsample frequency/period/scales
    freqs_ds = cwt_results['freqs'][::freq_factor]
    period_ds = cwt_results['period'][::freq_factor]
    scales_ds = cwt_results['scales'][::freq_factor]
    
    # Downsample 2D arrays - ensure real values only
    power_ds = np.real(cwt_results['power'][::freq_factor, ::time_factor])
    phase_ds = np.real(cwt_results['phase'][::freq_factor, ::time_factor])
    coherence_ds = np.real(cwt_results['coherence'][::freq_factor, ::time_factor])
    sig95_xwt_ds = np.real(cwt_results['sig95_xwt'][::freq_factor, ::time_factor])
    
    # Downsample 1D arrays - ensure real values only
    coi_ds = np.real(cwt_results['coi'][::time_factor])
    signif_xwt_ds = np.real(cwt_results['signif_xwt'][::freq_factor])
    global_power_ds = np.real(cwt_results['global_power'][::freq_factor])
    global_signif_ds = np.real(cwt_results['global_signif'][::freq_factor])
    scale_avg_power_ds = np.real(scale_avg_power[::time_factor])
    
    if VERBOSE and (time_factor > 1 or freq_factor > 1):
        print(f"  Downsampled: time {n_time}->{len(time_ds)}, freq {n_freq}->{len(freqs_ds)}")
    
    return {
        'time': time_ds.tolist(),
        'freqs': freqs_ds.tolist(),
        'period': period_ds.tolist(),
        'scales': scales_ds.tolist(),
        'power': power_ds.tolist(),
        'phase': phase_ds.tolist(),
        'coherence': coherence_ds.tolist(),
        'coi': coi_ds.tolist(),
        'sig95_xwt': sig95_xwt_ds.tolist(),
        'signif_xwt': signif_xwt_ds.tolist(),
        'global_power': global_power_ds.tolist(),
        'global_signif': global_signif_ds.tolist(),
        'scale_avg_power': scale_avg_power_ds.tolist(),
        'downsampling_factors': {
            'time_factor': time_factor,
            'freq_factor': freq_factor
        }
    }

def calculate_summary_statistics(cwt_results, time, scale_avg_power, scale_avg_signif):
    """
    Calculate summary statistics from cross-wavelet results.
    """
    power = cwt_results['power']
    coherence = cwt_results['coherence']
    phase = cwt_results['phase']
    freqs = cwt_results['freqs']
    period = cwt_results['period']
    coi = cwt_results['coi']
    scales = cwt_results['scales']
    global_power = cwt_results['global_power']
    global_signif = cwt_results['global_signif']
    
    # Create COI mask if requested
    if COI_EXCLUDE:
        coi_mask = np.zeros_like(power, dtype=bool)
        for i, c in enumerate(coi):
            coi_mask[:, i] = scales[:, np.newaxis].flatten() > c
    else:
        coi_mask = np.zeros_like(power, dtype=bool)
    
    # Mask out COI regions
    power_valid = np.ma.masked_array(power, coi_mask)
    coherence_valid = np.ma.masked_array(coherence, coi_mask)
    
    # Dominant frequency at each time point (outside COI)
    dominant_freq_idx = np.ma.argmax(power_valid, axis=0)
    dominant_freqs = freqs[dominant_freq_idx]
    
    # Convert to list, handling masked arrays
    if isinstance(dominant_freqs, np.ma.MaskedArray):
        dominant_freqs_list = dominant_freqs.filled(0).tolist()
    else:
        dominant_freqs_list = dominant_freqs.tolist()
    
    # Phase statistics (circular mean)
    mean_phase_by_freq = np.angle(np.mean(np.exp(1j * phase), axis=1))
    
    # Time-frequency regions of high coherence
    high_coherence_regions = coherence > HIGH_COHERENCE_THRESHOLD
    
    # Calculate percent of time each frequency shows high coherence
    high_coherence_by_freq = np.sum(high_coherence_regions & ~coi_mask, axis=1) / np.maximum(np.sum(~coi_mask, axis=1), 1)
    
    return {
        'global_power': global_power.tolist(),
        'global_signif': global_signif.tolist(),
        'dominant_freqs': dominant_freqs_list,
        'mean_phase_by_freq': mean_phase_by_freq.tolist(),
        'high_coherence_by_freq': high_coherence_by_freq.tolist(),
        'high_coherence_fraction': float(np.sum(high_coherence_regions & ~coi_mask) / np.sum(~coi_mask)) if np.sum(~coi_mask) > 0 else 0,
        'max_coherence': float(np.max(coherence_valid)) if coherence_valid.count() > 0 else 0,
        'mean_coherence': float(np.mean(coherence_valid)) if coherence_valid.count() > 0 else 0,
        'scale_avg_power_mean': float(np.mean(scale_avg_power)),
        'scale_avg_signif': float(scale_avg_signif),
        'coherence_threshold_used': HIGH_COHERENCE_THRESHOLD
    }

def process_cross_wavelet_pair(video_id, data_type1, data_type2, config):
    """
    Process cross-wavelet analysis for a pair of data types following pycwt standard.
    """
    if VERBOSE:
        print(f"\nProcessing cross-wavelet: {data_type1} vs {data_type2}")
    
    # Load both time series
    data1, time1, dt1, std1, var1 = load_and_prepare_timeseries(video_id, data_type1)
    data2, time2, dt2, std2, var2 = load_and_prepare_timeseries(video_id, data_type2)
    
    if data1 is None or data2 is None:
        if VERBOSE:
            print(f"  Failed to load data for {data_type1} or {data_type2}")
        return None
    
    # Ensure both series have the same time base
    # Find common time range
    t_start = max(time1[0], time2[0])
    t_end = min(time1[-1], time2[-1])
    
    # Check minimum overlap
    overlap_fraction = (t_end - t_start) / max(time1[-1] - time1[0], time2[-1] - time2[0])
    if overlap_fraction < MIN_COMMON_TIME_FRACTION:
        if VERBOSE:
            print(f"  Insufficient overlap ({overlap_fraction:.1%} < {MIN_COMMON_TIME_FRACTION:.1%})")
        return None
    
    # Use the smaller dt (higher sampling rate)
    dt = min(dt1, dt2)
    
    # Create common time array
    n_samples = int((t_end - t_start) / dt) + 1
    time_common = np.linspace(t_start, t_end, n_samples)
    
    # Interpolate both series to common time base
    if INTERPOLATION_METHOD == 'linear':
        data1_interp = np.interp(time_common, time1, data1)
        data2_interp = np.interp(time_common, time2, data2)
    elif INTERPOLATION_METHOD == 'cubic':
        from scipy.interpolate import interp1d
        f1 = interp1d(time1, data1, kind='cubic', fill_value='extrapolate')
        f2 = interp1d(time2, data2, kind='cubic', fill_value='extrapolate')
        data1_interp = f1(time_common)
        data2_interp = f2(time_common)
    else:
        data1_interp = np.interp(time_common, time1, data1)
        data2_interp = np.interp(time_common, time2, data2)
    
    if VERBOSE:
        print(f"  Common time base: {len(time_common)} points, dt={dt:.4f}")
    
    # Apply edge taper if requested
    if EDGE_TAPER:
        window = signal.tukey(len(time_common), alpha=TAPER_ALPHA)
        data1_interp = data1_interp * window
        data2_interp = data2_interp * window
        if DEBUG_MODE and VERBOSE:
            print(f"  Applied Tukey window with α={TAPER_ALPHA}")
    
    # Compute cross-wavelet transform using standard approach
    cwt_results = compute_cross_wavelet_standard(
        data1_interp, data2_interp, time_common, dt,
        mother=MOTHER_WAVELET, omega0=OMEGA0, dj=DJ
    )
    
    # Calculate scale-averaged wavelet power
    period = cwt_results['period']
    power = cwt_results['power']
    
    # Determine scale-averaging band
    if SCALE_AVG_BAND_AUTO:
        avg_period_min = S0_FACTOR * dt
        avg_period_max = min(SCALE_AVG_MAX_PERIOD * dt, period[-1] / 2)
    else:
        avg_period_min = SCALE_AVG_MIN_PERIOD * dt
        avg_period_max = min(SCALE_AVG_MAX_PERIOD * dt, period[-1])
    
    sel = find((period >= avg_period_min) & (period <= avg_period_max))
    
    if len(sel) > 0:
        # Scale-averaged power
        Cdelta = cwt_results['mother'].cdelta
        # Create scale matrix properly for broadcasting: (n_scales, n_times)
        scale_avg = cwt_results['scales'][:, np.newaxis] * np.ones((1, len(time_common)))
        scale_avg = power / scale_avg
        scale_avg_power = cwt_results['dj'] * dt / Cdelta * scale_avg[sel, :].sum(axis=0)
        
        # Significance for scale-averaged power
        scale_avg_signif, _ = wavelet.significance(
            1.0, dt, cwt_results['scales'], 2, 
            np.mean([cwt_results['alpha1'], cwt_results['alpha2']]),
            significance_level=SIGNIFICANCE_LEVEL,
            dof=[cwt_results['scales'][sel[0]], cwt_results['scales'][sel[-1]]],
            wavelet=cwt_results['mother']
        )
        
        if VERBOSE:
            print(f"  Scale-averaging band: {avg_period_min:.2f} - {avg_period_max:.2f}")
    else:
        scale_avg_power = np.zeros(len(time_common))
        scale_avg_signif = 0
        if VERBOSE:
            print("  Warning: No scales in averaging band")
    
    # Calculate summary statistics
    stats = calculate_summary_statistics(cwt_results, time_common, scale_avg_power, scale_avg_signif)
    
    # Downsample for storage
    downsampled = downsample_for_storage(cwt_results, time_common, scale_avg_power)
    
    # Prepare output data structure
    result = {
        'data_type1': data_type1,
        'data_type2': data_type2,
        'dt': float(dt),
        'time_range': [float(t_start), float(t_end)],
        'n_samples': int(n_samples),
        'mother_wavelet': MOTHER_WAVELET,
        'omega0': OMEGA0 if MOTHER_WAVELET.lower() == 'morlet' else None,
        'dj': float(cwt_results['dj']),
        's0': float(cwt_results['s0']),
        'J': float(cwt_results['J']),
        'alpha1': float(cwt_results['alpha1']),
        'alpha2': float(cwt_results['alpha2']),
        'statistics': stats,
        'scale_avg_band': [float(avg_period_min), float(avg_period_max)],
        'visualization': downsampled
    }
    
    # Add metadata if requested
    if SAVE_METADATA:
        result['metadata'] = {
            'detrend': DETREND_DATA,
            'detrend_order': DETREND_ORDER if DETREND_DATA else None,
            'edge_taper': EDGE_TAPER,
            'taper_alpha': TAPER_ALPHA if EDGE_TAPER else None,
            'significance_level': SIGNIFICANCE_LEVEL,
            'coherence_threshold': HIGH_COHERENCE_THRESHOLD,
            'interpolation_method': INTERPOLATION_METHOD
        }
    
    # Save full resolution if requested
    if SAVE_FULL_RESOLUTION:
        result['full_resolution'] = {
            'power': np.real(cwt_results['power']).tolist(),
            'coherence': np.real(cwt_results['coherence']).tolist(),
            'phase': np.real(cwt_results['phase']).tolist()
        }
        if VERBOSE:
            print("  Warning: Full resolution data saved (large file size)")
    
    return result

def main():
    parser = argparse.ArgumentParser(description='Generate Cross-Wavelet data for DIMS Dashboard')
    parser.add_argument('--config', default='config.json', help='Path to config.json')
    parser.add_argument('--output-dir', default=OUTPUT_DIR, help='Output directory for cross-wavelet data')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose output')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    args = parser.parse_args()
    
    # Override global settings if command-line args provided
    global VERBOSE, DEBUG_MODE
    if args.verbose:
        VERBOSE = True
    if args.debug:
        DEBUG_MODE = True
        VERBOSE = True
    
    # Load config
    with open(args.config, 'r') as f:
        config = json.load(f)
    
    # Check if cross-wavelet is requested
    if 'include_crosswavelet' not in config or not config['include_crosswavelet']:
        if VERBOSE:
            print("No cross-wavelet analysis requested in config")
        return
    
    cwt_data_types = config['include_crosswavelet']
    
    if len(cwt_data_types) < 2:
        print("Error: Need at least 2 data types for cross-wavelet analysis")
        return
    
    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)
    
    # Print configuration summary if verbose
    if VERBOSE:
        print("\n" + "="*60)
        print("CROSS-WAVELET ANALYSIS CONFIGURATION")
        print("="*60)
        print(f"Mother Wavelet: {MOTHER_WAVELET}")
        if MOTHER_WAVELET.lower() == 'morlet':
            print(f"  Omega0: {OMEGA0}")
        print(f"Frequency Resolution (dj): {DJ}")
        print(f"Smallest Scale Factor: {S0_FACTOR}")
        print(f"Significance Level: {SIGNIFICANCE_LEVEL*100}%")
        print(f"Detrending: {DETREND_DATA} (order {DETREND_ORDER})" if DETREND_DATA else "Detrending: False")
        print(f"Edge Tapering: {EDGE_TAPER} (α={TAPER_ALPHA})" if EDGE_TAPER else "Edge Tapering: False")
        print(f"High Coherence Threshold: {HIGH_COHERENCE_THRESHOLD}")
        print(f"Visualization Resolution: {MAX_TIME_POINTS_VIZ} × {MAX_FREQ_POINTS_VIZ}")
        print(f"Output Format: {OUTPUT_FORMAT}")
        print("="*60)
    
    # Process each video
    for video_id in config['videoIDs']:
        if VERBOSE:
            print(f"\n{'='*50}")
            print(f"Processing video: {video_id}")
            print(f"{'='*50}")
        
        # Process all pairs
        cwt_results = {}
        
        # Determine which pairs to compute (only between different data types)
        pairs_to_compute = []
        for i in range(len(cwt_data_types)):
            for j in range(i + 1, len(cwt_data_types)):
                pairs_to_compute.append((cwt_data_types[i], cwt_data_types[j]))
                # If symmetric pairs requested, also add reverse
                if SYMMETRIC_PAIRS:
                    pairs_to_compute.append((cwt_data_types[j], cwt_data_types[i]))
        
        if VERBOSE:
            print(f"Computing {len(pairs_to_compute)} pair(s)")
        
        # Process each pair
        for data_type1, data_type2 in pairs_to_compute:
            pair_key = f"{data_type1}_vs_{data_type2}"
            
            result = process_cross_wavelet_pair(
                video_id, data_type1, data_type2, config
            )
            
            if result:
                cwt_results[pair_key] = result
        
        # Save results
        if cwt_results:
            # Determine output format and save
            if OUTPUT_FORMAT in ['json', 'both']:
                output_path = os.path.join(args.output_dir, f"{video_id}_crosswavelet_data.json")
                
                output_data = {
                    'video_id': video_id,
                    'crosswavelet_pairs': cwt_results,
                    'data_types': cwt_data_types,
                    'config': {
                        'mother_wavelet': MOTHER_WAVELET,
                        'omega0': OMEGA0 if MOTHER_WAVELET.lower() == 'morlet' else None,
                        'dj': DJ,
                        's0_factor': S0_FACTOR,
                        'significance_level': SIGNIFICANCE_LEVEL,
                        'high_coherence_threshold': HIGH_COHERENCE_THRESHOLD,
                        'detrend': DETREND_DATA,
                        'edge_taper': EDGE_TAPER
                    },
                    'processing_info': {
                        'pairs_computed': len(cwt_results),
                        'coi_excluded_from_stats': COI_EXCLUDE,
                        'visualization_resolution': f"{MAX_TIME_POINTS_VIZ}x{MAX_FREQ_POINTS_VIZ}"
                    }
                }
                
                with open(output_path, 'w') as f:
                    json.dump(output_data, f, indent=2)
                
                print(f"\nSaved cross-wavelet data to {output_path}")
            
            if OUTPUT_FORMAT in ['npz', 'both']:
                # Save as compressed NumPy format for easier loading in Python
                output_path_npz = os.path.join(args.output_dir, f"{video_id}_crosswavelet_data.npz")
                np.savez_compressed(output_path_npz, **cwt_results)
                print(f"Saved cross-wavelet data to {output_path_npz}")
            
            # Print summary
            if VERBOSE:
                print("\nSummary of Cross-Wavelet Analysis:")
                for pair_key, result in cwt_results.items():
                    stats = result['statistics']
                    print(f"\n  {pair_key}:")
                    print(f"    - Max coherence: {stats['max_coherence']:.3f}")
                    print(f"    - Mean coherence: {stats['mean_coherence']:.3f}")
                    print(f"    - High coherence fraction: {stats['high_coherence_fraction']*100:.1f}%")
                    print(f"    - Time points: {len(result['visualization']['time'])}")
                    print(f"    - Frequency bins: {len(result['visualization']['freqs'])}")
                    print(f"    - AR1 coefficients: α₁={result['alpha1']:.3f}, α₂={result['alpha2']:.3f}")
    
    print("\nCross-wavelet processing complete!")
    
    if not VERBOSE:
        print("\nNote: Run with --verbose flag for detailed output")
    print("Note: Install required packages with:")
    print("  pip install pycwt numpy pandas scipy")

if __name__ == "__main__":
    main()

"""
GPU configuration utilities for TensorFlow.

This module provides functions to configure TensorFlow for optimal GPU usage
on different platforms:
- Apple Silicon (M1/M2/M3) Macs with tensorflow-metal
- Linux with NVIDIA GPUs and CUDA
- CPU-only fallback

Prevents common issues like bus errors and memory allocation failures.
"""
import os
import sys
import warnings

# Set environment variables BEFORE TensorFlow is imported anywhere
# Platform-specific GPU memory settings
if sys.platform == 'linux':
	# Linux with NVIDIA CUDA - use async memory allocator
	os.environ.setdefault('TF_GPU_ALLOCATOR', 'cuda_malloc_async')
	os.environ.setdefault('TF_FORCE_GPU_ALLOW_GROWTH', 'true')
elif sys.platform == 'darwin':
	# macOS with Metal - these don't apply but set growth flag anyway
	os.environ.setdefault('TF_FORCE_GPU_ALLOW_GROWTH', 'true')


def configure_gpu_memory_growth():
	"""
	Configure TensorFlow to use memory growth on GPU devices.
	
	This prevents TensorFlow from allocating all GPU memory at once,
	which can cause:
	- Bus errors on Apple Silicon Macs
	- OOM errors on Linux with NVIDIA GPUs
	
	Should be called BEFORE importing any other TensorFlow modules
	or creating any TensorFlow operations.
	
	Returns:
		bool: True if GPU configuration was successful, False otherwise.
	"""
	try:
		import tensorflow as tf
		
		# Get list of physical GPU devices
		gpus = tf.config.list_physical_devices('GPU')
		
		if gpus:
			for gpu in gpus:
				try:
					# Enable memory growth - allocate memory as needed
					tf.config.experimental.set_memory_growth(gpu, True)
				except RuntimeError as e:
					# Memory growth must be set before GPUs are initialized
					# This is not an error if TensorFlow was already configured
					if "must be set before GPUs have been initialized" in str(e) or \
					   "virtual devices configured" in str(e):
						# Already configured, that's fine
						return True
					warnings.warn(f"Could not set memory growth for {gpu}: {e}")
					return False
			
			return True
		else:
			# No GPUs found, CPU will be used
			return True
			
	except Exception as e:
		warnings.warn(f"GPU configuration failed: {e}")
		return False


def disable_gpu():
	"""
	Disable GPU and force TensorFlow to use CPU only.
	
	Useful as a fallback when GPU causes issues.
	"""
	os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
	os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
	
	try:
		import tensorflow as tf
		tf.config.set_visible_devices([], 'GPU')
	except Exception:
		pass


def get_device_info():
	"""
	Get information about available compute devices.
	
	Returns:
		dict: Information about available GPUs and CPUs.
	"""
	try:
		import tensorflow as tf
		
		gpus = tf.config.list_physical_devices('GPU')
		cpus = tf.config.list_physical_devices('CPU')
		
		return {
			'gpus': [str(gpu) for gpu in gpus],
			'cpus': [str(cpu) for cpu in cpus],
			'gpu_count': len(gpus),
			'using_gpu': len(gpus) > 0,
			'platform': sys.platform
		}
	except Exception as e:
		return {
			'error': str(e),
			'gpus': [],
			'cpus': [],
			'gpu_count': 0,
			'using_gpu': False,
			'platform': sys.platform
		}

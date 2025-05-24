#!/usr/bin/env python
"""
Helper script to run Django commands with environment variables loaded.
This script ensures that environment variables are properly loaded before
running Django management commands.
"""
import os
import sys
from pathlib import Path

def load_env_file(file_path):
    """Load environment variables from a .env file"""
    if not Path(file_path).exists():
        print(f"Warning: .env file not found at {file_path}")
        return False
    
    with open(file_path) as f:
        for line in f:
            line = line.strip()
            
            # Skip comments and empty lines
            if not line or line.startswith('#') or line.startswith('//'):
                continue
                
            # Parse the key-value pair
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                
                # Remove quotes if present
                if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
                    value = value[1:-1]
                    
                os.environ[key] = value
    
    return True

def main():
    # Try to load environment variables from .env files
    base_dir = Path(__file__).resolve().parent
    
    # Try project root first, then django directory
    env_paths = [
        base_dir.parent / '.env',
        base_dir / '.env',
    ]
    
    env_loaded = False
    for env_path in env_paths:
        if load_env_file(str(env_path)):
            print(f"Loaded environment variables from {env_path}")
            env_loaded = True
    
    if not env_loaded:
        print("Warning: Could not load any .env files. Using default values.")
    
    # Ensure critical environment variables have default values
    if 'SECRET_KEY' not in os.environ:
        os.environ['SECRET_KEY'] = 'django-insecure-x)v@)fdg7tkqf#l8$4=br!g00w4*4+19sb(p+s=(^a%-*en)tr'
        print("Using default SECRET_KEY for development.")
    
    if 'FERNET_SECRET_KEY' not in os.environ:
        os.environ['FERNET_SECRET_KEY'] = 'pSD0ZVXNIHPUzPcHwf1DBMgHjli3M6dBW011JA3991I='
        print("Using default FERNET_SECRET_KEY for development.")
    
    # Set Django settings module
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'admin.settings')
    
    # Import Django and run command
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    
    execute_from_command_line(sys.argv[1:])  # Skip this script's name

if __name__ == '__main__':
    main()

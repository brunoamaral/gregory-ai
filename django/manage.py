#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path
from gregory.utils.model_utils import DenseTransformer

def main():
    """Run administrative tasks."""
    # Default settings module
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'admin.settings')
    
    # Try to load environment variables directly if python-dotenv is available
    try:
        from dotenv import load_dotenv
        
        # Try multiple locations for .env file
        base_dir = Path(__file__).resolve().parent
        potential_paths = [
            base_dir.parent / '.env',  # Project root
            base_dir / '.env',         # Django directory
        ]
        
        for env_path in potential_paths:
            if env_path.exists():
                load_dotenv(dotenv_path=env_path)
                print(f"Loaded environment variables from {env_path}")
                break
        
        # Set default SECRET_KEY if not available
        if 'SECRET_KEY' not in os.environ:
            os.environ['SECRET_KEY'] = 'django-insecure-x)v@)fdg7tkqf#l8$4=br!g00w4*4+19sb(p+s=(^a%-*en)tr'
            print("Temporary SECRET_KEY generated for development.")
        
        # Set default FERNET_SECRET_KEY if not available
        if 'FERNET_SECRET_KEY' not in os.environ:
            os.environ['FERNET_SECRET_KEY'] = 'pSD0ZVXNIHPUzPcHwf1DBMgHjli3M6dBW011JA3991I='
            print("Temporary FERNET_SECRET_KEY generated for development.")
    except ImportError:
        # If python-dotenv is not available, still ensure we have fallback values
        if 'SECRET_KEY' not in os.environ:
            os.environ['SECRET_KEY'] = 'django-insecure-x)v@)fdg7tkqf#l8$4=br!g00w4*4+19sb(p+s=(^a%-*en)tr'
            print("Temporary SECRET_KEY generated for development.")
        
        if 'FERNET_SECRET_KEY' not in os.environ:
            os.environ['FERNET_SECRET_KEY'] = 'pSD0ZVXNIHPUzPcHwf1DBMgHjli3M6dBW011JA3991I='
            print("Temporary FERNET_SECRET_KEY generated for development.")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()

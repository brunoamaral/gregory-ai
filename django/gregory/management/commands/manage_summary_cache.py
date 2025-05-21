# filepath: /Users/brunoamaral/Labs/gregory/django/gregory/management/commands/manage_summary_cache.py
from django.core.management.base import BaseCommand
import os

from gregory.utils.summariser import get_cache_stats, clear_cache, _get_cache_file


class Command(BaseCommand):
    """
    Management command to work with text summarization cache.
    
    This allows viewing cache statistics, clearing the cache, or pruning old entries.
    """
    help = "Manage the text summarization cache"
    
    def add_arguments(self, parser):
        parser.add_argument(
            "--stats",
            action="store_true",
            help="Show statistics about the summary cache",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear all cached summaries",
        )
        
    def handle(self, *args, **options):
        if options["stats"]:
            # Get cache statistics
            stats = get_cache_stats()
            
            # Get cache file info if it exists
            cache_file = _get_cache_file()
            file_size = "N/A"
            if os.path.exists(cache_file):
                file_size = f"{os.path.getsize(cache_file) / (1024*1024):.2f} MB"
            
            self.stdout.write(self.style.SUCCESS("Summary Cache Statistics"))
            self.stdout.write(f"Cache entries: {stats['cache_entries']}")
            self.stdout.write(f"Cache hits: {stats['hits']}")
            self.stdout.write(f"Cache misses: {stats['misses']}")
            self.stdout.write(f"Hit rate: {stats['hit_rate']}")
            self.stdout.write(f"Cache file size: {file_size}")
            self.stdout.write(f"Cache location: {cache_file}")
        
        elif options["clear"]:
            result = clear_cache()
            self.stdout.write(self.style.SUCCESS(f"Cache cleared. {result['entries']} entries removed."))
        
        else:
            self.stdout.write(self.style.WARNING("Please specify an action: --stats or --clear"))
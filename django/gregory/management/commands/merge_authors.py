from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from gregory.models import Authors, Articles
from django.db.models import Q, Count
import re


class Command(BaseCommand):
    help = 'Merges authors with the same ORCID. Provide an ORCID as argument to merge all authors with that ORCID into one. Handles both http and https ORCID variants.'

    def add_arguments(self, parser):
        parser.add_argument(
            'orcid',
            type=str,
            help='The ORCID to search for and merge authors (can be full URL or just the ID)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be merged without making changes'
        )
        parser.add_argument(
            '--keep-author',
            type=int,
            help='Specify the author_id to keep when merging (default: prefers https version, then most articles, then earliest created)'
        )

    def normalize_orcid(self, orcid):
        """
        Extract the ORCID ID from various formats and return both http and https variants.
        
        Args:
            orcid (str): ORCID in any format
            
        Returns:
            tuple: (orcid_id, http_variant, https_variant)
        """
        if not orcid or not orcid.strip():
            return None, None, None
            
        # Remove whitespace
        orcid = orcid.strip()
        
        # Extract the ORCID ID using regex
        # Matches patterns like: 0000-0000-0000-0000
        orcid_pattern = r'(\d{4}-\d{4}-\d{4}-\d{3}[\dX])'
        match = re.search(orcid_pattern, orcid)
        
        if match:
            orcid_id = match.group(1)
            http_variant = f"http://orcid.org/{orcid_id}"
            https_variant = f"https://orcid.org/{orcid_id}"
            return orcid_id, http_variant, https_variant
        else:
            # If no standard ORCID ID found, treat the input as-is
            # This handles edge cases where the ORCID might be malformed
            return orcid, orcid, orcid

    def handle(self, *args, **options):
        orcid = options['orcid']
        dry_run = options['dry_run']
        keep_author_id = options.get('keep_author')
        
        if not orcid:
            raise CommandError('You must provide an ORCID argument.')
        
        # Normalize ORCID input - handle both empty strings and None
        if not orcid.strip():
            raise CommandError('ORCID cannot be empty.')

        # Normalize the ORCID to find both http and https variants
        orcid_id, http_variant, https_variant = self.normalize_orcid(orcid)
        
        if not orcid_id:
            raise CommandError(f'Invalid ORCID format: {orcid}')

        # Find all authors with either the http or https variant of the ORCID
        authors_query = Q(ORCID=http_variant) | Q(ORCID=https_variant)
        
        # Also search for the raw ORCID ID in case it's stored without the URL
        if orcid_id not in [http_variant, https_variant]:
            authors_query |= Q(ORCID=orcid_id)
        
        authors_with_orcid = Authors.objects.filter(authors_query).annotate(
            article_count=Count('articles')
        ).order_by('-article_count', 'author_id')
        
        if not authors_with_orcid.exists():
            self.stdout.write(
                self.style.WARNING(f'No authors found with ORCID: {orcid} (searched for {orcid_id}, {http_variant}, {https_variant})')
            )
            return
        
        if authors_with_orcid.count() == 1:
            author = authors_with_orcid.first()
            self.stdout.write(
                self.style.SUCCESS(
                    f'Only one author found with ORCID {orcid}: '
                    f'{author.full_name} (ID: {author.author_id}) - ORCID stored as: {author.ORCID}'
                )
            )
            return

        # Display found authors with their ORCID variants
        self.stdout.write(f'\nFound {authors_with_orcid.count()} authors with ORCID variants of {orcid_id}:')
        self.stdout.write('-' * 100)
        
        for author in authors_with_orcid:
            https_indicator = "✓ HTTPS" if author.ORCID and "https://" in author.ORCID else "  HTTP "
            self.stdout.write(
                f'{https_indicator} | ID: {author.author_id:5} | '
                f'Name: {author.full_name:25} | '
                f'Articles: {author.article_count:3} | '
                f'ORCID: {author.ORCID}'
            )

        # Determine which author to keep
        if keep_author_id:
            try:
                author_to_keep = authors_with_orcid.get(author_id=keep_author_id)
            except Authors.DoesNotExist:
                raise CommandError(
                    f'Author with ID {keep_author_id} not found among authors with ORCID variants of {orcid_id}'
                )
        else:
            # Priority: 1) HTTPS version, 2) Most articles, 3) Earliest created (lowest ID)
            https_authors = [a for a in authors_with_orcid if a.ORCID and "https://" in a.ORCID]
            
            if https_authors:
                # Sort HTTPS authors by article count (desc) then by ID (asc)
                https_authors.sort(key=lambda a: (-a.article_count, a.author_id))
                author_to_keep = https_authors[0]
                self.stdout.write(f'\n📌 Prioritizing HTTPS ORCID version: {author_to_keep.ORCID}')
            else:
                # No HTTPS version found, use the default logic
                author_to_keep = authors_with_orcid.first()
                self.stdout.write(f'\n⚠️  No HTTPS ORCID version found, using default selection')

        authors_to_merge = authors_with_orcid.exclude(author_id=author_to_keep.author_id)

        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(
            f'MERGE PLAN:\n'
            f'  KEEPING: {author_to_keep.full_name} (ID: {author_to_keep.author_id}, '
            f'Articles: {author_to_keep.article_count})\n'
            f'           ORCID: {author_to_keep.ORCID}\n'
            f'  MERGING: {", ".join([f"{a.full_name} (ID: {a.author_id})" for a in authors_to_merge])}'
        )
        
        # Show ORCID variants being merged
        for author in authors_to_merge:
            self.stdout.write(f'           Merging ORCID: {author.ORCID}')

        # If keeping an author with HTTP, suggest updating to HTTPS
        if author_to_keep.ORCID and "http://" in author_to_keep.ORCID and "https://" not in author_to_keep.ORCID:
            suggested_https = author_to_keep.ORCID.replace("http://", "https://")
            self.stdout.write(f'\n💡 SUGGESTION: Consider updating the kept author\'s ORCID to HTTPS: {suggested_https}')

        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN - No changes will be made'))
            
            # Show which articles would be transferred
            for author in authors_to_merge:
                articles = author.articles_set.all()
                if articles.exists():
                    self.stdout.write(f'\nArticles from {author.full_name} (ID: {author.author_id}) that would be transferred:')
                    for article in articles:
                        self.stdout.write(f'  - {article.title[:80]}...')
                else:
                    self.stdout.write(f'\n{author.full_name} (ID: {author.author_id}) has no articles to transfer')
            
            return

        # Confirm the merge
        self.stdout.write('\n' + '!' * 80)
        confirmation = input(
            'Are you sure you want to proceed with this merge? '
            'This action cannot be undone! Type "yes" to continue: '
        )
        
        if confirmation.lower() != 'yes':
            self.stdout.write(self.style.WARNING('Merge cancelled.'))
            return

        # Perform the merge within a transaction
        try:
            with transaction.atomic():
                total_articles_transferred = 0
                
                # Check if we should update the ORCID to HTTPS
                should_update_orcid = False
                https_orcid = None
                
                # Look for an HTTPS version in the authors being merged
                for author in authors_to_merge:
                    if author.ORCID and "https://" in author.ORCID:
                        https_orcid = author.ORCID
                        should_update_orcid = True
                        break
                
                # If the author we're keeping has HTTP and we found HTTPS, update it
                if (should_update_orcid and author_to_keep.ORCID and 
                    "http://" in author_to_keep.ORCID and "https://" not in author_to_keep.ORCID):
                    old_orcid = author_to_keep.ORCID
                    author_to_keep.ORCID = https_orcid
                    self.stdout.write(f'\n🔄 Updating ORCID from {old_orcid} to {https_orcid}')
                
                for author in authors_to_merge:
                    # Get all articles associated with this author
                    articles = author.articles_set.all()
                    
                    if articles.exists():
                        self.stdout.write(
                            f'\nTransferring {articles.count()} articles from '
                            f'{author.full_name} (ID: {author.author_id}) to '
                            f'{author_to_keep.full_name} (ID: {author_to_keep.author_id})...'
                        )
                        
                        # Transfer articles to the author we're keeping
                        for article in articles:
                            # Remove the article from the old author
                            article.authors.remove(author)
                            # Add the article to the author we're keeping (if not already associated)
                            if not article.authors.filter(author_id=author_to_keep.author_id).exists():
                                article.authors.add(author_to_keep)
                                total_articles_transferred += 1
                            else:
                                self.stdout.write(
                                    f'  Article "{article.title[:50]}..." already associated with '
                                    f'{author_to_keep.full_name}'
                                )
                    
                    # Update the author_to_keep with the best available information
                    if not author_to_keep.given_name and author.given_name:
                        author_to_keep.given_name = author.given_name
                    if not author_to_keep.family_name and author.family_name:
                        author_to_keep.family_name = author.family_name
                    if not author_to_keep.country and author.country:
                        author_to_keep.country = author.country
                    if not author_to_keep.orcid_check and author.orcid_check:
                        author_to_keep.orcid_check = author.orcid_check
                
                # Save the updated author_to_keep
                author_to_keep.save()
                
                # Delete the duplicate authors
                deleted_count = 0
                for author in authors_to_merge:
                    self.stdout.write(f'Deleting author: {author.full_name} (ID: {author.author_id})')
                    author.delete()
                    deleted_count += 1

                self.stdout.write('\n' + '=' * 80)
                self.stdout.write(
                    self.style.SUCCESS(
                        f'MERGE COMPLETED SUCCESSFULLY!\n'
                        f'  Authors merged: {deleted_count}\n'
                        f'  Articles transferred: {total_articles_transferred}\n'
                        f'  Remaining author: {author_to_keep.full_name} (ID: {author_to_keep.author_id})\n'
                        f'  Final ORCID: {author_to_keep.ORCID}'
                    )
                )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error during merge: {str(e)}')
            )
            raise CommandError(f'Merge failed: {str(e)}')

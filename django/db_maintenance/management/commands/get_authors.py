from django.core.management.base import BaseCommand
from crossref.restful import Works, Etiquette
from dotenv import load_dotenv
import os
from django.utils import timezone
from gregory.models import Articles, Authors
from sitesettings.models import CustomSetting
from django.db.models import Q

class Command(BaseCommand):
    help = 'Fetches authors from CrossRef and updates the database.'

    def handle(self, *args, **kwargs):
        load_dotenv()
        SITE = CustomSetting.objects.get(site__domain=os.environ.get('DOMAIN_NAME'))
        CLIENT_WEBSITE = 'https://' + SITE.site.domain + '/'
        my_etiquette = Etiquette(SITE.title, 'v8', CLIENT_WEBSITE, SITE.admin_email)
        works = Works(etiquette=my_etiquette)

        articles = Articles.objects.filter(Q(authors__isnull=True, doi__isnull=False, crossref_check__lte=timezone.now(), crossref_check__gt=timezone.now()-timezone.timedelta(days=30)) | Q(authors__isnull=True, doi__isnull=False, crossref_check__isnull=True))
        for article in articles:
            w = works.doi(article.doi)
            if w and 'author' in w and w['author']:
                for author_data in w['author']:
                    # Ensure we have the necessary information
                    given_name = author_data.get('given')
                    family_name = author_data.get('family')
                    orcid = author_data.get('ORCID')

                    if not given_name or not family_name:
                        self.stdout.write(f"Missing given name or family name, skipping this author. Article DOI: {article.doi}.")
                        continue

                    # First, attempt to match or create by ORCID
                    if orcid:
                        author_obj, created = Authors.objects.get_or_create(ORCID=orcid, defaults={'given_name': given_name, 'family_name': family_name})
                        if not created:
                            # Update the author name if it's different
                            if author_obj.given_name != given_name or author_obj.family_name != family_name:
                                author_obj.given_name = given_name
                                author_obj.family_name = family_name
                                author_obj.save()
                                self.stdout.write(f"Updated author {author_obj.full_name} with ORCID: {orcid}.")
                    else:
                        # Handle authors without ORCID or when ORCID isn't provided
                        try:
                            author_obj = Authors.objects.get(given_name=given_name, family_name=family_name)
                        except Authors.DoesNotExist:
                            # Create a new author if none found
                            print(f"Creating author: {given_name} {family_name} with ORCID: {orcid}")
                            author_obj = Authors.objects.create(given_name=given_name, family_name=family_name, ORCID=orcid)
                        except Authors.MultipleObjectsReturned:
                            self.stdout.write(f"Multiple authors found for {given_name} {family_name}, unable to uniquely identify. Skipping.")
                            continue

                    # Add author to article if an author object was successfully created or retrieved
                    if author_obj:
                        article.authors.add(author_obj)

            article.crossref_check = timezone.now()
            article.save()

        self.stdout.write(self.style.SUCCESS('Successfully updated authors from CrossRef.'))

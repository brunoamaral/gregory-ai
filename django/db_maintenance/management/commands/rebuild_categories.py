from django.core.management.base import BaseCommand
import os
import psycopg2
from dotenv import load_dotenv

class Command(BaseCommand):
	help = 'Rebuilds category associations for articles and trials.'

	def handle(self, *args, **options):
		self.rebuild_cats_articles()
		self.rebuild_cats_trials()
		self.stdout.write(self.style.SUCCESS('Successfully rebuilt category associations for articles and trials.'))

	def rebuild_cats_articles(self):
		load_dotenv()
		# Get environment variables
		db_host = os.getenv('DB_HOST')
		postgres_user = os.getenv('POSTGRES_USER')
		postgres_password = os.getenv('POSTGRES_PASSWORD')
		postgres_db = os.getenv('POSTGRES_DB')

		try:
			conn = psycopg2.connect(dbname=postgres_db, user=postgres_user, host=db_host, password=postgres_password)
		except Exception as e:
			self.stdout.write(f"Unable to connect to the database: {e}")
			return

		cur = conn.cursor()
		cur.execute("DELETE FROM articles_categories;")
		cur.execute("SELECT category_name, category_terms, category_id FROM categories;")
		categories = cur.fetchall()

		for cat in categories:
			terms = f"(%{'%|%'.join(cat[1]).lower()}%)"
			cat_id = cat[2]
			cur.execute("""INSERT INTO articles_categories (articles_id, categories_id)
						   SELECT "articles"."article_id", "categories"."category_id" FROM "categories"
						   INNER JOIN "articles" ON lower("articles"."title") SIMILAR TO %s AND "categories"."category_id" = %s
						""", (terms, cat_id))
			conn.commit()

	def rebuild_cats_trials(self):
		load_dotenv()
		# Repeat similar steps for trials_categories
		db_host = os.getenv('DB_HOST')
		postgres_user = os.getenv('POSTGRES_USER')
		postgres_password = os.getenv('POSTGRES_PASSWORD')
		postgres_db = os.getenv('POSTGRES_DB')

		try:
			conn = psycopg2.connect(dbname=postgres_db, user=postgres_user, host=db_host, password=postgres_password)
		except Exception as e:
			self.stdout.write(f"Unable to connect to the database: {e}")
			return

		cur = conn.cursor()
		cur.execute("DELETE FROM trials_categories;")
		cur.execute("SELECT category_name, category_terms, category_id FROM categories;")
		categories = cur.fetchall()

		for cat in categories:
			terms = f"(%{'%|%'.join(cat[1]).lower()}%)"
			cat_id = cat[2]
			cur.execute("""INSERT INTO trials_categories (trials_id, categories_id)
						   SELECT "trials"."trial_id", "categories"."category_id" FROM "categories"
						   INNER JOIN "trials" ON lower("trials"."title") SIMILAR TO %s AND "categories"."category_id" = %s
						""", (terms, cat_id))
			conn.commit()

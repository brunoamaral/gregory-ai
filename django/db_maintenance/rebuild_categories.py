import os
import psycopg2
from dotenv import load_dotenv
from django.conf import settings
from django_cron import CronJobBase, Schedule

class RebuildCats(CronJobBase):
	RUN_EVERY_MINS = 30
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'db_maintenance.rebuild_categories'
	def do(self):
		load_dotenv()
		## GET ENV
		db_host = os.getenv('DB_HOST')
		postgres_user = os.getenv('POSTGRES_USER')
		postgres_password = os.getenv('POSTGRES_PASSWORD')
		postgres_db = os.getenv('POSTGRES_DB')
		try:
			conn = psycopg2.connect("dbname='"+ postgres_db +"' user='" + postgres_user + "' host='" + db_host + "' password='" + postgres_password + "'")
		except:
			print("I am unable to connect to the database")
		cur = conn.cursor()
		cur.execute("DELETE FROM articles_categories ;")
		cur.execute("SELECT category_name,category_terms,category_id FROM categories;")
		categories = cur.fetchall()
		for cat in categories:
			terms = f"(%{'%|%'.join(cat[1]).lower()}%)"
			cat_id = cat[2]
			cur.execute("""INSERT INTO articles_categories (articles_id,categories_id)
			SELECT "articles"."article_id", "categories"."category_id" FROM "categories" INNER JOIN "articles" ON "articles"."title" similar to %s AND "categories"."category_id" = %s 
			""", (terms,cat_id))

			cur.connection.commit()
	pass
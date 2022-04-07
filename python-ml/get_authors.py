from crossref.restful import Works, Etiquette
import os
import psycopg2
from dotenv import load_dotenv
load_dotenv()
## GET ENV
db_host = os.getenv('DB_HOST')
# db_host = 'localhost'
postgres_user = os.getenv('POSTGRES_USER')
postgres_password = os.getenv('POSTGRES_PASSWORD')
postgres_db = os.getenv('POSTGRES_DB')

try:
	conn = psycopg2.connect("dbname='"+ postgres_db +"' user='" + postgres_user + "' host='" + db_host + "' password='" + postgres_password + "'")
except:
	print("I am unable to connect to the database")

cur = conn.cursor()
cur.execute("SELECT article_id,doi FROM articles WHERE doi IS NOT NULL;")
articles = cur.fetchall()

my_etiquette = Etiquette('Gregory MS', 'v8', 'https://gregory-ms.com', 'bruno@gregory-ms.com')
works = Works(etiquette=my_etiquette)

works = Works(etiquette=my_etiquette)

for article in articles:
	w = works.doi(article[1])
	if w is not None and 'author' in w and w['author'] is not None:
		authors = w['author']
		article_id = article[0]
		for author in authors:
			author_first_name = None
			if 'given' in author:
				author_first_name = author['given']
			author_family_name = None
			if 'family' in author:
				author_family_name = author['family']
			author_orcid = None
			if 'ORCID' in author:
				author_orcid = author['ORCID']

			# check if author name + author family name exists in database
			author_query = """SELECT "author_id" FROM authors WHERE given_name = %s AND family_name = %s ;"""
			cur = conn.cursor()
			cur.execute(author_query, (author_first_name,author_family_name))
			author_id = cur.fetchone()
			if author_id == None:
				# if author in authors_list:
				## add to database
				if author_first_name is not None and author_family_name is not None:
					add_author = """INSERT INTO "public"."authors" ("given_name", "family_name", "ORCID") VALUES (%s, %s, %s) RETURNING author_id;"""
					cur.execute(add_author, (author_first_name,author_family_name,author_orcid))
					conn.commit()
					author_id = cur.fetchone()[0]
					cur.execute("""INSERT INTO "public"."articles_authors" ("authors_id","articles_id") VALUES  (%s,%s); """, (author_id, article_id))
					conn.commit()
			else:
				# does this relationship exist?
				cur.execute("""SELECT count(*) from "public"."articles_authors" WHERE articles_id = %s AND authors_id = %s;""", (article_id,author_id[0]))
				count = cur.fetchone()[0]
				if count == 0:
					# if we have the data, insert the relation of article + entity
					cur.execute("""INSERT INTO "public"."articles_authors" ("authors_id","articles_id") VALUES  (%s,%s); """, (author_id[0], article_id))
					conn.commit()
				else:
					print('relationship exists. Author: ', author_id[0], " article_id: ", article_id)
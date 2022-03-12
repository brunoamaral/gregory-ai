from dotenv import load_dotenv
import os
import scispacy
import spacy 
import psycopg2

load_dotenv()
## GET ENV
db_host = os.getenv('DB_HOST')
postgres_user = os.getenv('POSTGRES_USER')
postgres_password = os.getenv('POSTGRES_PASSWORD')
postgres_db = os.getenv('POSTGRES_DB')


# List of entites we want to save
# entities_list = ['GPE','PERSON','ORG']
## GET DATA

try:
	con = psycopg2.connect("dbname='"+ postgres_db +"' user='" + postgres_user + "' host='" + db_host + "' password='" + postgres_password + "'")
except:
	print("I am unable to connect to the database")

query = "SELECT articles.article_id,articles.title FROM articles INNER JOIN sources ON articles.source = sources.source_id WHERE articles.published_date BETWEEN NOW() - INTERVAL '30 DAYS' AND NOW() AND sources.language = 'en';"
# query = "SELECT articles.article_id,articles.title FROM articles INNER JOIN sources ON articles.source = sources.source_id WHERE  sources.subject = '" + subject + "' AND sources.language = 'en';"

cur = con.cursor()
cur.execute(query)
data = cur.fetchall()
# nlp = spacy.load("en_core_web_sm")
# nlp = spacy.load("en_core_sci_sm")
# nlp = spacy.load("en_core_sci_md")
nlp = spacy.load("en-core-sci-lg")


for item in data:
	article_id = item[0]
	print(article_id)
	text = item[1]
	doc = nlp(text)
	for ent in doc.ents:
		# Print the entity text and its label
		print(ent.text, ent.label_)
		# check if entity + label exists in database
		ent_query = """SELECT "id" FROM entities WHERE entity = %s AND label = %s;"""
		cur = con.cursor()
		cur.execute(ent_query, (ent.text,ent.label_))
		entitity_id = cur.fetchone()
		if entitity_id == None:
			# if ent.label_ in entities_list:
			## add to database
			add_entity = """INSERT INTO "public"."entities" ("entity", "label") VALUES (%s, %s) RETURNING id;"""
			cur.execute(add_entity, (ent.text,ent.label_))
			con.commit()
			entitity_id = cur.fetchone()[0]
			cur.execute("""INSERT INTO "public"."articles_entities" ("entities_id","articles_id") VALUES  (%s,%s); """, (entitity_id, article_id))
			con.commit()
		else:
			# does this relationship exist?
			cur.execute("""SELECT count(*) from "public"."articles_entities" WHERE articles_id = %s AND id = %s;""", (article_id,entitity_id[0]))
			count = cur.fetchone()[0]
			if count == 0:
				# if we have the data, insert the relation of article + entity
				cur.execute("""INSERT INTO "public"."articles_entities" ("entities_id","articles_id") VALUES  (%s,%s); """, (entitity_id[0], article_id))
				con.commit()
			else:
				print('relationship exists. Article: ', article_id, " Entity: ", entitity_id[0])
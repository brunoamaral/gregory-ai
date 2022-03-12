import json
import psycopg2
import pandas as pd
import numpy as np
from os import path
from PIL import Image
from wordcloud import WordCloud, STOPWORDS, ImageColorGenerator
import matplotlib.pyplot as plt

import os
from dotenv import load_dotenv
load_dotenv()
## GET ENV
db_host = os.getenv('DB_HOST')
postgres_user = os.getenv('POSTGRES_USER')
postgres_password = os.getenv('POSTGRES_PASSWORD')
postgres_db = os.getenv('POSTGRES_DB')

## GET DATA
try:
	conn = psycopg2.connect("dbname='"+ postgres_db +"' user='" + postgres_user + "' host='" + db_host + "' password='" + postgres_password + "'")
except:
	print("I am unable to connect to the database")

df = pd.read_sql_query('''
SELECT
	*
FROM
	articles
WHERE
	published_date BETWEEN NOW() - INTERVAL '7 DAYS'
	AND NOW()
    AND noun_phrases IS NOT NULL;
''', conn)
words = []

for row in df.noun_phrases:
    try:
        row = json.loads(row)
    except:
        if row != "":
            print(row)
        pass
    for item in row:
        words.append(item)

unique_string=(',').join(words)
print('generating image')


wordcloud = WordCloud(
                    width = 800,
                    height = 400,
                    background_color='white',
                    colormap='Greens',
                    prefer_horizontal=1,
                    contour_width=0).generate(unique_string)

image=plt.figure(figsize=(15,8),frameon=False)
ax = plt.Axes(image, [0., 0., 1., 1.])
ax.set_axis_off()
image.add_axes(ax)
plt.imshow(wordcloud)
plt.axis("off")
plt.savefig("/data/public/tagcloud"+".png", bbox_inches='tight')
plt.show()
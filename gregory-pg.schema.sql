-- -------------------------------------------------------------
-- TablePlus 4.5.2(402)
--
-- https://tableplus.com/
--
-- Database: creamdb
-- Generation Time: 2022-03-03 01:15:04.8210
-- -------------------------------------------------------------


DROP TABLE IF EXISTS "public"."articles";
-- This script only contains the table creation statements and does not fully represent the table in the database. It's still missing: indices, triggers. Do not use it as a backup.

-- Sequence and defined type
CREATE SEQUENCE IF NOT EXISTS articles_article_id_seq;

-- Table Definition
CREATE TABLE "public"."articles" (
    "article_id" int4 NOT NULL DEFAULT nextval('articles_article_id_seq'::regclass),
    "title" text,
    "summary" text,
    "link" text,
    "published_date" timestamp,
    "relevant" bool,
    "ml_prediction_gnb" bool,
    "ml_prediction_lr" bool,
    "sent_to_admin" bool,
    "sent_to_subscribers" bool,
    "sent_to_twitter" bool,
    "noun_phrases" json,
    "source" int8,
    "discovery_date" timestamp NOT NULL,
    PRIMARY KEY ("article_id")
);

DROP TABLE IF EXISTS "public"."categories";
-- This script only contains the table creation statements and does not fully represent the table in the database. It's still missing: indices, triggers. Do not use it as a backup.

-- Sequence and defined type
CREATE SEQUENCE IF NOT EXISTS categories_category_id_seq;

-- Table Definition
CREATE TABLE "public"."categories" (
    "category_id" int4 NOT NULL DEFAULT nextval('categories_category_id_seq'::regclass),
    "category_name" text,
    PRIMARY KEY ("category_id")
);

DROP TABLE IF EXISTS "public"."entities";
-- This script only contains the table creation statements and does not fully represent the table in the database. It's still missing: indices, triggers. Do not use it as a backup.

-- Sequence and defined type
CREATE SEQUENCE IF NOT EXISTS entities_id_seq;

-- Table Definition
CREATE TABLE "public"."entities" (
    "id" int4 NOT NULL DEFAULT nextval('entities_id_seq'::regclass),
    "entity" text NOT NULL,
    "label" text NOT NULL,
    PRIMARY KEY ("id")
);

DROP TABLE IF EXISTS "public"."rel_articles_categories";
-- This script only contains the table creation statements and does not fully represent the table in the database. It's still missing: indices, triggers. Do not use it as a backup.

-- Table Definition
CREATE TABLE "public"."rel_articles_categories" (
    "article_id" int8 NOT NULL,
    "category_id" int8 NOT NULL
);

DROP TABLE IF EXISTS "public"."rel_articles_entities";
-- This script only contains the table creation statements and does not fully represent the table in the database. It's still missing: indices, triggers. Do not use it as a backup.

-- Sequence and defined type
CREATE SEQUENCE IF NOT EXISTS rel_articles_entities_id_seq;

-- Table Definition
CREATE TABLE "public"."rel_articles_entities" (
    "id" int4 NOT NULL DEFAULT nextval('rel_articles_entities_id_seq'::regclass),
    "entity_id" int4,
    "article_id" int4,
    PRIMARY KEY ("id")
);

DROP TABLE IF EXISTS "public"."sources";
-- This script only contains the table creation statements and does not fully represent the table in the database. It's still missing: indices, triggers. Do not use it as a backup.

-- Sequence and defined type
CREATE SEQUENCE IF NOT EXISTS sources_source_id_seq;

-- Table Definition
CREATE TABLE "public"."sources" (
    "source_id" int4 NOT NULL DEFAULT nextval('sources_source_id_seq'::regclass),
    "name" text,
    "link" text,
    "language" text NOT NULL DEFAULT 'en'::text,
    "subject" text NOT NULL DEFAULT ''::text,
    "method" text NOT NULL DEFAULT 'rss'::text,
    PRIMARY KEY ("source_id")
);

-- Column Comment
COMMENT ON COLUMN "public"."sources"."subject" IS 'what is the subject of this source (crypto, polytics, science, etc.)';
COMMENT ON COLUMN "public"."sources"."method" IS 'how we should fetch the data';

ALTER TABLE "public"."articles" ADD FOREIGN KEY ("source") REFERENCES "public"."sources"("source_id");
ALTER TABLE "public"."rel_articles_categories" ADD FOREIGN KEY ("category_id") REFERENCES "public"."categories"("category_id");
ALTER TABLE "public"."rel_articles_categories" ADD FOREIGN KEY ("article_id") REFERENCES "public"."articles"("article_id");
ALTER TABLE "public"."rel_articles_entities" ADD FOREIGN KEY ("article_id") REFERENCES "public"."articles"("article_id");
ALTER TABLE "public"."rel_articles_entities" ADD FOREIGN KEY ("entity_id") REFERENCES "public"."entities"("id");

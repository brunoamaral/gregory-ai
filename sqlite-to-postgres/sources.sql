-- -------------------------------------------------------------
-- TablePlus 4.6.0(406)
--
-- https://tableplus.com/
--
-- Database: gregory.db
-- Generation Time: 2022-03-03 22:33:02.3170
-- -------------------------------------------------------------


INSERT INTO "sources" ("source_id", "name", "link", "language", "subject", "method") VALUES
('1', 'APTA', 'https://www.apta.org/search?Q=%22Multiple+Sclerosis%22+OR+%22autoimmune+encephalomyelitis%22+OR+encephalomyelitis+OR+%22immune+tolerance%22+OR+myelin&searcharticletypes=8834&searchconditionandsymptoms=&searchloc=APTA', 'en', 'MS', 'scrape'),
('2', 'BioMedCentral', 'https://www.biomedcentral.com/search?searchType=publisherSearch&sort=PubDate&page=1&query=Multiple+Sclerosis', 'en', 'MS', 'scrape'),
('3', 'FASEB', 'https://faseb.onlinelibrary.wiley.com/action/showFeed?ui=0&mi=2h5krp8&type=search&feed=rss&query=%2526content%253DarticlesChapters%2526field1%253DAllField%2526publication%253D15306860%2526target%253Ddefault%2526text1%253DMultiple%252BSclerosis%252BOR%252Bautoimmune%252Bencephalomyelitis%252BOR%252Bencephalomyelitis%252BOR%252Bimmune%252Btolerance%252BOR%252Bmyelin', 'en', 'MS', 'rss'),
('4', 'JNeuroSci', 'https://www.jneurosci.org/search/text_abstract_title%3AMultiple%2BSclerosis%20text_abstract_title_flags%3Amatch-phrase%20exclude_meeting_abstracts%3A1%20numresults%3A50%20sort%3Apublication-date%20direction%3Adescending%20format_result%3Astandard', 'en', 'MS', 'scrape'),
('5', 'MS & Rel. Disorders', 'https://www.msard-journal.com/action/doSearch?text1=Multiple+Sclerosis&field1=AbstractTitleKeywordFilterField&startPage=0&sortBy=Earliest', 'en', 'MS', 'scape'),
('6', 'Nature.com', 'https://www.nature.com/', 'en', 'MS', 'manual'),
('7', 'PEDro', 'https://search.pedro.org.au/advanced-search/results?abstract_with_title=Multiple+Sclerosis&therapy=0&problem=0&body_part=0&subdiscipline=0&topic=0&method=0&authors_association=&title=&source=&year_of_publication=&date_record_was_created=&nscore=&perpage=20&lop=or&find=&find=Start+Search', 'en', 'MS', 'scrape'),
('8', 'PubMed', 'https://pubmed.ncbi.nlm.nih.gov/rss/search/10guX6I3SqrbUeeLKSTD6FCRM44ewnrN2MKKTQLLPMHB4xNsZU/?limit=15&utm_campaign=pubmed-2&fc=20210216052009', 'en', 'MS', 'rss'),
('9', 'Sage Pub', 'https://journals.sagepub.com/action/doSearch?AllField=multiple+sclerosis&SeriesKey=msja&content=articlesChapters&countTerms=true&target=default&sortBy=Ppub&startPage=&ContentItemType=research-article', 'en', 'MS', 'scrape'),
('10', 'Scielo', 'https://search.scielo.org/?q=Multiple+Sclerosis&lang=en&count=15&from=0&output=site&sort=&format=summary&fb=&page=1&q=%22Multiple+Sclerosis%22+OR+%22autoimmune+encephalomyelitis%22+OR+encephalomyelitis+OR+%22immune+tolerance%22+OR+myelin&lang=en&page=1', 'en', 'MS', 'scrape'),
('11', 'The Lancet', 'https://www.thelancet.com/action/doSearch?text1=%22Multiple+Sclerosis%22+OR+%22autoimmune+encephalomyelitis%22+OR+encephalomyelitis+OR+%22immune+tolerance%22+OR+myelin&field1=AbstractTitleKeywordFilterField&startPage=0&sortBy=Earliest', 'en', 'MS', 'scrape');


-- Converts source column from string to ID 
update articles set source = 1 where source = "APTA";
update articles set source = 2 where source = "BioMedCentral";
update articles set source = 3 where source = "FASEB";
update articles set source = 4 where source = "JNeuroSci";
update articles set source = 5 where source = "MS & Rel. Disorders";
update articles set source = 6 where source = "Nature.com";
update articles set source = 7 where source = "PEDro";
update articles set source = 8 where source = "pubmed";
update articles set source = 9 where source = "Sage Pub";
update articles set source = 10 where source = "Scielo";
update articles set source = 11 where source = "The Lancet";
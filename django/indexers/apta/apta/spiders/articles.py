import scrapy

class ArticlesSpider(scrapy.Spider):
		name = "articles"
		start_urls = [
						'https://www.apta.org/search?Q=%22Multiple+Sclerosis%22+OR+%22autoimmune+encephalomyelitis%22+OR+encephalomyelitis+OR+%22immune+tolerance%22+OR+myelin&searcharticletypes=8834&searchconditionandsymptoms=&searchloc=APTA',
				]

		def parse(self, response):
			# page = response.url.split("/")[-2]
			# filename = f'articles-{page}.html'
			# with open(filename, 'wb') as f:
			# 		f.write(response.body)
			# self.log(f'Saved file {filename}')
			for article in response.css('.h5.fw-700.fs-25px.color-gray-700.mb-7px.d-inline-block'):
				yield {
					'title': article.css('a::text').get(),
					'link': 'https://www.apta.org' + article.css('a::attr(href)').get()
        }


# >>> response.css('.h5.fw-700.fs-25px.color-gray-700.mb-7px.d-inline-block::text').getall()
# >>> articles  = response.css('.h5.fw-700.fs-25px.color-gray-700.mb-7px.d-inline-block')
# >>> for article in articles:
#         article.css('a::text').get()

# 'Multiple Sclerosis in Adults: Management [NICE CG186]'
# 'Multiple Sclerosis (MS) Quality of Life Inventory '
# 'Rivermead Mobility Index for Multiple Sclerosis (MS)'
# 'Four Square Step Test (FSST) for Multiple Sclerosis (MS)'
# 'Hauser Ambulation Index for Multiple Sclerosis'
# 'Berg Balance Scale (BBS) for Multiple Sclerosis (MS)'
# 'Activities-specific Balance Confidence (ABC) Scale for Multiple Sclerosis (MS) '
# 'Expanded Disability Status Scale (EDSS) '
# 'Multiple Sclerosis (MS) Functional Composite (MSFC) '
# 'Functional Reach Test for Multiple Sclerosis (MS)'


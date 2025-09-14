from django.shortcuts import render

def acknowledgements(request):
	"""
	Display acknowledgements page showing contributors to the project.
	"""
	context = {
		'page_title': 'Acknowledgements',
		'contributors': [
			{
				'name': 'NovaSBE - Nova School of Business and Economics',
				'description': 'Developed the enhanced Machine Learning pipeline for clinical research article classification. Their team implemented a PubMed BERT model achieving 96.5% recall for identifying relevant Multiple Sclerosis research papers.',
				'project_details': 'GregoryAIxNovaSBE - Classification of Relevant Multiple Sclerosis Articles',
				'team_members': [
					'Julia Antonioli',
					'Kuba Bialczyk',
					'Nicolò Mazzoleni',
					'Francisco Gomes',
					'Martim Esteves'
				],
				'repository': 'files_repo_PBL_nsbe',
				'github_url': 'https://github.com/Data-Science-Knowledge-Center-Nova-SBE/gregory_nsbe_pbl/tree/main',
				'license': 'GNU General Public License v3.0'
			}
		]
	}
	return render(request, 'gregory/acknowledgements.html', context)

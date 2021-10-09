//import $ from "jquery";

// import instantsearch from 'instantsearch.js/es';

// import { hits, searchBox } from "instantsearch.js/es/widgets";


const search = instantsearch({
    appId: '5DE3PYXB8W',
    apiKey: '26e30f4528e8117200c40a23d8c943f9',
    indexName: 'gregory',
    searchFunction: function(helper) {
        var searchResults = document.getElementsByClassName('ais-hits');
        if (helper.state.query === '') {
            //searchResults.hide();
            return;
        }
        helper.search();
        //searchResults.show();
    }
});

var hitTemplate =
    '<div class="hit media">' +
    '<div class="media-left">' +
    '<img src="{{image}}" />' +
    '</div>' +
    '<div class="media-body">' +
    '<h4 class="media-heading">{{{_highlightResult.title.value}}} {{#stars}}<span class="ais-star-rating--star{{^.}}__empty{{/.}}"></span>{{/stars}}</h4>' +
    '<p class="content">{{_snippetResult.content.value}}</p>' +
    '</div>' +
    '</div>';

var hitTemplateCard = `


<div class="card card-plain card-blog">
                                <div class="row">
                                    <div class="col-md-12">
                                        
                                        <h3 class="card-title">
                                            <a href="{{uri}}">{{title}}</a>  
                                        </h3>
                                        <p class="card-description">
                                            
                                            <br>
                                            <a href="{{uri}}">Continue Reading </a>
                                        </p>
                                        <p class="author">
                                            
                                            <time class="published text-muted" itemprop="datePublished" datetime=" 2021-10-01T17:56:36 ">October 1, 2021</time>
                                            <span class="badge badge-info text-white font-weight-normal">pubmed</span>  <span class="badge badge-primary text-white font-weight-normal">human</span>
                                        </p>
                                    </div>
                                </div>
                            </div>
                            `;


var noResultsTemplate =
    '<div class="text-center">No results found matching <strong>{{query}}</strong>.</div>';

search.addWidget(
    instantsearch.widgets.searchBox({
        container: '.form-group',
        placeholder: 'Search',
        cssClasses: { input: 'search-input hideInput form-control' },
        autofocus: true,
        poweredBy: false,
        magnifier: false,
        reset: false
    })
);

search.addWidget(
    instantsearch.widgets.hits({
        container: 'div.search-hits',
        templates: {
            empty: noResultsTemplate,
            item: hitTemplateCard
        },
        hitsPerPage: 9
    })
);

search.start();
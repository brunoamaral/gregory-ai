---
title: "Observatorio"
date: 2021-08-12T15:33:35+01:00
lastmod: 
author: Bruno Amaral

description: 
categories: []
tags: []

draft: false
enableDisqus : true
enableMathJax: false
disableToC: false
disableAutoCollapse: true
---

Esta página tem uma secção para cada uma das terapias listadas como relevantes pela [MS Society](https://www.mssociety.org.uk/research/explore-our-research/emerging-research-and-treatments/explore-treatments-in-trials).

Em cada secção, são listados os ensaios clínicos e artigos publicados em que o fármaco consta do título.


<h3 id="Ocrelizumab">Ocrelizumab</h3>
<ol class="articles Ocrelizumab"></ol>
<h4>Ensaios Clínicos</h4>

<ol class="trials Ocrelizumab"></ol>

<h3 id="Metformin">Metformin</h3>
<ol class="articles Metformin"></ol>
<h4>Ensaios Clínicos</h4>
<ol class="trials Metformin"></ol>

<script>

const queries = document.querySelectorAll('h3')

function searchArticles(term){
  // let list = document.querySelector('ol.articles.'+ term );
  let articleRequest = new Request('https://api.brunoamaral.net/articles/keyword/'+ term );

  fetch(articleRequest).then(response => response.json()).then(data => {console.log(data)})

  fetch(articleRequest )
    .then(response => response.json())
    .then(data => {
      let list = document.querySelector('ol.articles.'+term);
      for (const item of data) {
        let listItem = document.createElement('li');
        listItem.appendChild(
          document.createElement('strong')
        ).textContent = item.title;

        let a = listItem.appendChild(document.createElement('a'));
          a.textContent = `${item.source}`;
          a.href = `${item.link}`;
        list.appendChild(listItem);
      }
    })
    .catch(console.error);
}

function searchTrials(term){
  // let list = document.querySelector('ol.trials.'+ term );
  let trialsRequest = new Request('https://api.brunoamaral.net/trials/keyword/'+ term );
  fetch(trialsRequest).then(response => response.json()).then(data => {console.log(data)})

  fetch(trialsRequest )
    .then(response => response.json())
    .then(data => {
      let list = document.querySelector('ol.trials.'+term);
      for (const item of data) {
        let listItem = document.createElement('li');
        listItem.appendChild(
          document.createElement('strong')
        ).textContent = item.title;

        let a = listItem.appendChild(document.createElement('a'));
          a.textContent = `${item.source}`;
          a.href = `${item.link}`;
        list.appendChild(listItem);
      }
    })
    .catch(console.error);
}


for (let i = 0; i < queries.length; i++) {
  // console.log(queries[i].id)
  searchArticles(queries[i].id)
  searchTrials(queries[i].id)
}
</script>
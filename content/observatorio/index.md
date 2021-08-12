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

<ul></ul>

<script>
const myList = document.querySelector('ul');
const myRequest = new Request('https://api.brunoamaral.net/articles/relevant');

fetch(myRequest )
  .then(response => response.json())
  .then(data => {
    for (const product of data.products) {
      let listItem = document.createElement('li');
      listItem.appendChild(
        document.createElement('strong')
      ).textContent = product.Name;
      listItem.append(
        ` can be found in ${
          product.Location
        }. Cost: `
      );
      listItem.appendChild(
        document.createElement('strong')
      ).textContent = `£${product.Price}`;
      myList.appendChild(listItem);
    }
  })
  .catch(console.error);
</script>
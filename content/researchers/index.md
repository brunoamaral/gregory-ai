---
authors:
  - bruno-amaral
date: 2021-09-23T20:36:59+01:00
description: ""
draft: false
layout: page
resources: 
- src: images/julia-koblitz-RlOAwXt2fEA-unsplash.jpeg
  name: "header"
- src: "gallery/*.jpg"
  name: gallery-:counter
  title: gallery-title-:counter
- src:
  name: slide-1
rowclasses: justify-content-center align-self-center
slug:
subtitle: We help you focus your research and find related articles with ease
tags: 
  - 
categories: 
  - 
title: "For Researchers"

options:
  unlisted: false
  showHeader: true
  hideFooter: false
  hideSubscribeForm: false
  header: mini
scripts:
  - '<script src="/js/mermaid.min.js"></script>'
menu:
  main:
    url: researchers
    name: Researchers
    weight: 3
---

<div class="col-md-5 col-12 justify-content-center align-self-center align-right ">
  <img src="images/undraw_Online_articles_re_yrkj.svg" class="float-right w-50 align-middle d-none d-md-block" alt="medical doctors" loading="lazy"/>
  </div>
  <div class="col-md-5 col-12 justify-content-center align-self-center">
  
  <h3 class="title">Current Research</h3>
  
  <p class="lead font-weight-biold">You can browse the most up to date research articles, download our database, and receive a free newsletter with the most relevant articles.</p>
      <a href='{{< ref "/articles/_index.md" >}}' class="btn btn-primary btn-round btn-lg font-weight-bold">View articles <i class="fas fa-arrow-circle-right"></i></a>
      <a href='{{< ref "/downloads/_index.md" >}}' class="btn btn-success btn-round btn-lg font-weight-bold">Download the database <i class="fas fa-download"></i></a>
  </div>
</div>

<div class="row justify-content-center align-self-center mb-5 p-md-5">
  <div class="col-md-5 col-12 justify-content-center align-self-center">
    <h3 class="title">Research Digest</h3>
    <p class="lead font-weight-biold">We have a weekly digest with the most recent and relevant research to keep you updated.</p>
    <p>Send us an email and ask to be subscribed.</p>
    <a href='mailto:mail@brunoamaral.eu' class="btn btn-primary btn-round btn-lg font-weight-bold">Send Email <i class="fas fa-envelope"></i></a>
    </div>
  <div class="col-md-5 col-12 justify-content-center align-self-center">
    <img src="images/undraw_subscribe_vspl.svg" class="w-50 align-middle d-none d-md-block" alt="Email newsletter" loading="lazy"/>
  </div>  
</div>

<div class="row justify-content-center align-self-center mb-5 p-md-5">
<div class="col-md-5 col-12 justify-content-center align-self-center align-right ">
  <img src="images/undraw_medicine_b1ol.svg" class="w-50 align-middle d-none d-md-block float-left" alt="medical doctors" loading="lazy" />
  </div>
  <div class="col-md-5 col-12 justify-content-center align-self-center">
  
  <h3 class="title">The observatory</h3>
  
  <p class="lead font-weight-biold">On this page you will find a listing of promissing medicine and therapies with their associated articles and clinical trials.</p>
  
  <p>An item is added to the list based on what is identified by the MS Society Website, or when there is an associated clinical trial.</p>
  <a href='{{< ref "/observatory/_index.md" >}}' class="btn btn-success btn-round btn-lg font-weight-bold">Observatory <i class="fas fa-arrow-circle-right"></i></a>
</div>  
</div>

<div class="row justify-content-center align-self-center mb-5 p-md-5">
<div class="col-md-12"><h3 class="title text-center">Where the information comes from</h3></div>
<div class="mermaid col-md-10 mx-auto">
graph TD;
    APTA[fa:fa-newspaper APTA.org] -->Gregory;
    BioMedCentral[fa:fa-newspaper BioMedCentral.com] -->Gregory;
    JNeurosci[fa:fa-newspaper JNeurosci.org]-->Gregory;
    PEDro[fa:fa-newspaper PEDro.org.au] -->Gregory;
    PubMed[fa:fa-newspaper PubMed.gov] -->Gregory;
    Reuters[fa:fa-newspaper Reuters Health]-->Gregory;
    Scielo[fa:fa-newspaper Scielo.org] -->Gregory;
    TheLancet[fa:fa-newspaper The Lancet Health]-->Gregory;
    MsRelDis[fa:fa-newspaper MS and Related Disorders]-->Gregory;
    Manual[fa:fa-keyboard Manual Input]-->Gregory;
    Gregory{fa:fa-robot Gregory}-->Website(fa:fa-globe Website)
    Gregory{fa:fa-robot Gregory}-->Newsletter(fa:fa-envelope Newsletter)
</div>
<div class="col-md-12 text-center">
  <a href='{{< ref "/about/index.md" >}}' class="btn btn-primary btn-round btn-lg font-weight-bold">More information on the about page <i class="fas fa-arrow-circle-right"></i></a>
</div>
</div>


<!-- TO DO : 

Listagem dos resultados mais recentes nos últimos 30 dias

listagem dos resultados mais relevantes 

link para o observatorio

cta para newsletter

-->





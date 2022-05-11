---
authors:
  - bruno-amaral
date: 2021-09-23T20:37:07+01:00
description: ""
draft: false
layout: page
rowclasses: justify-content-center align-self-center
resources: 
- src: images/national-cancer-institute-NFvdKIhxYlU-unsplash.jpeg
  name: "header"
- src: "gallery/*.jpg"
  name: gallery-:counter
  title: gallery-title-:counter
- src:
  name: slide-1
slug:
subtitle: "This site exists to save you time in finding the latest research to help your patients."
tags: 
  - 
categories: 
  - 
title: "For Doctors"
menu:
  main:
    weight: 2
    name: Doctors
options:
  unlisted: false
  showHeader: true
  hideFooter: false
  hideSubscribeForm: false
  header: mini
scripts:
  - '<script src="/js/mermaid.min.js"></script>'
---



<div class="col-md-5 col-12 justify-content-center align-self-center align-right ">
  <img src="images/undraw_medicine_b1ol.svg" class="w-50 align-middle d-none d-md-block float-right" alt="medical doctors" loading="lazy"/>
  </div>
  <div class="col-md-5 col-12 justify-content-center align-self-center">
  
  <h3 class="title">The observatory</h3>
  
  <p class="lead font-weight-biold">On this page you will find a listing of promissing medicine and therapies with their associated articles and clinical trials.</p>
  
  <p>An item is added to the list based on what is identified by the MS Society Website, or when there is an associated clinical trial.</p>
  <a href='{{< ref "/observatory/_index.md" >}}' class="btn btn-success btn-round btn-lg font-weight-bold umami--click--doctors-page-observatory">Observatory <i class="fas fa-arrow-circle-right"></i></a>
  
  </div>
</div>

<div class="row justify-content-center align-self-center mb-5 p-md-5">
  <div class="col-md-5 col-12 align-self-center">
    <h3 class="title">Research Digest</h3>
    <p class="lead font-weight-biold">We have a weekly digest with the most recent and relevant research to keep you updated.</p>
    <p>Send us an email and ask to be subscribed.</p>
    <a href='mailto:mail@brunoamaral.eu' class="btn btn-primary btn-round btn-lg font-weight-bold umami--click--doctors-page-send-email">Send Email <i class="fas fa-envelope"></i></a>
    </div>
  <div class="col-md-5 col-12 align-self-center">
    <img src="images/undraw_subscribe_vspl.svg" class="w-50 align-middle d-none d-md-block" alt="Email newsletter" loading="lazy" />
  </div>  
</div>


<div class="row justify-content-center align-self-center mb-5 p-md-5">
  <div class="col-md-5 col-12 align-self-center">
    <img src="images/undraw_medical_research_qg4d.svg" class="w-50 align-middle d-none d-md-block" alt="Email newsletter" loading="lazy" />
  </div>  
  <div class="col-md-5 col-12 justify-content-center align-self-center">
    <h3 class="title">Clinical Trials</h3>
    <p class="lead font-weight-biold">We do the best we can to identify clinical trials for Multiple Sclerosis and list them.</p>
    <a href='{{< ref "/trials/_index.md" >}}' class="btn btn-success btn-round btn-lg font-weight-bold umami--click--doctors-page-view-trials">View latest clinical trials <i class="fas fa-arrow-circle-right"></i></a>
    </div>
</div>

<div class="row justify-content-center align-self-center mb-5 p-md-5">
  <div class="col-12 align-self-center">
{{< metabase-embed dashboard="8" width="1300" height="1000" >}}
  </div>
</div>

<div class="row justify-content-center align-self-center mb-5 p-md-5">
  <div class="col-md-5 col-12 align-self-center">
    <h3 class="title">Download the articles</h3>
    <p class="lead font-weight-biold">The database is free to everyone and can be downloaded in both Excel and Json files.</p>
    <a href='/developers/articles.zip' target="_blank" class="btn btn-primary btn-round btn-lg font-weight-bold umami--click--doctors-page-download-articles-zip"> <i class="fas fa-file-archive"></i> Download Articles</a>
    </div>
  <div class="col-md-5 col-12 align-self-center">
    <img src="images/undraw_export_files_re_99ar.svg" class="w-50 align-middle d-none d-md-block" alt="Email newsletter" loading="lazy" />
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
  <a href='{{< ref "/about/index.md" >}}' class="btn btn-primary btn-round btn-lg font-weight-bold umami--click--doctors-page-link-about-page">More information on the about page <i class="fas fa-arrow-circle-right"></i></a>
</div>
</div>


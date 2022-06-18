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

<div class="col-md-5 col-12 justify-content-center align-self-center align-right">
  <img src="images/undraw_Online_articles_re_yrkj.svg" class="float-right w-50 align-middle d-none d-md-block" alt="medical doctors" loading="lazy"/>
  </div>
  <div class="col-md-5 col-12 justify-content-center align-self-center">
  
  <h3 class="title">Current Research</h3>
  
  <p class="lead font-weight-biold">You can browse the most up to date research articles, download our database, and receive a free newsletter with the most relevant articles.</p>
      <a href='{{< ref "/articles/_index.md" >}}' class="btn btn-primary btn-round btn-lg font-weight-bold umami--click--view-articles-researchers-page">View articles <i class="fas fa-arrow-circle-right"></i></a>
      <a href='{{< ref "/downloads/_index.md" >}}' class="btn btn-success btn-round btn-lg font-weight-bold umami--click--downloads-researchers-page">Download the database <i class="fas fa-download"></i></a>
  </div>
</div>

<div class="row justify-content-center align-self-center mb-5 mt-5 p-md-5">
  <div class="col-md-5 col-12 justify-content-center align-self-center ">
    <div class="col-md-12 ml-auto mr-auto">
                <div class="card card-contact card-raised">
                  <form role="form" id="contact-form1" method="post" action="https://api.gregory-ms.com/subscriptions/new/">
                    <div class="card-header text-center">
                      <h4 class="card-title font-weight-bold">Weekly digest of relevant papers</h4>
                      <p class="p-3">Every tuesday, and email with the latest research filtered by Machine Learning and human review.</p>
                    </div>
                    <div class="card-body">
                      <div class="row">
                        <div class="col-md-6 pr-2">
                          <label>First name</label>
                          <div class="input-group">
                            <div class="input-group-prepend">
                              <span class="input-group-text pr-2"><i class="now-ui-icons users_circle-08"></i></span>
                            </div>
                            <input type="text" name="first_name" class="form-control" placeholder="First Name..." aria-label="First Name..." autocomplete="given-name">
                          </div>
                        </div>
                        <div class="col-md-6 pl-2">
                          <div class="form-group">
                            <label>Last name</label>
                            <div class="input-group">
                              <div class="input-group-prepend">
                                <span class="input-group-text pr-2"><i class="now-ui-icons text_caps-small"></i></span>
                              </div>
                              <input type="text" name="last_name" class="form-control" placeholder="Last Name..." aria-label="Last Name..." autocomplete="family-name">
                            </div>
                          </div>
                        </div>
                      </div>
                      <div class="form-group">
                        <label>Email address</label>
                        <div class="input-group">
                          <div class="input-group-prepend">
                            <span class="input-group-text pr-2"><i class="now-ui-icons ui-1_email-85"></i></span>
                          </div>
                          <input type="email" name="email" id="email" class="form-control" placeholder="Email Here..." autocomplete="email">
                        </div>
                      </div>
                      <div class="form-group">
                        <label>I am a...</label>
                        <div class="input-group">
                          <select id="profile" name="profile" class="form-control">
                            <option value="researcher">researcher</option>
                            <option value="doctor">doctor</option>
                            <option value="clinical centre">clinical centre</option>
                            <option value="patient">patient</option>
                          </select>
                        </div>
                      </div>
                      <div class="row">
                        <div class="col-md-12 ml-auto mr-auto text-center">
                          <input value="2" name="list" id="list" type="hidden">
                          <button type="submit" class="btn btn-primary btn-round mr-auto ml-auto">Subscribe</button>
                        </div>
                      </div>
                    </div>
                  </form>
                </div>
              </div>
  </div>
  <div class="col-md-5 col-12 justify-content-center align-self-center">
    <img src="images/undraw_subscribe_vspl.svg" class="w-50 align-middle d-none d-md-block ml-auto mr-auto" alt="Email newsletter" loading="lazy"/>
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
  <a href='{{< ref "/observatory/_index.md" >}}' class="btn btn-success btn-round btn-lg font-weight-bold umami--click--observatory-researchers-page">Observatory <i class="fas fa-arrow-circle-right"></i></a>
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
  <a href='{{< ref "/about/index.md" >}}' class="btn btn-primary btn-round btn-lg font-weight-bold umami--click--more-info-on-sources-researchers-page">More information on the about page <i class="fas fa-arrow-circle-right"></i></a>
</div>
</div>
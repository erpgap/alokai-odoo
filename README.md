# Vuestorefront 3 - Odoo Backend Modules

## Overview

Vuestorefront 3 is a lightning-fast frontend platform for Headless Commerce.
In some situations it's ok to just use an Odoo template and start your website, but some
companies they need and full blown eCommerce platform because their business depends on that.

The Headless platform it decouples your storefront from the content management system and backend.
Odoo is the ultimate open source base ERP with many millions of customers around the globe, so it's the match made in open source heaven.

This is not another sync between Odoo and other eCommerce, data will always be in Odoo only.

## Purpose

You will need these modules installed in your Odoo to publish the GraphQL endpoints that VSF3 needs.

## How to install

- Firstly, ensure that the module file is present in the add-ons directory of the Odoo 
  server ``` git clone --recurse-submodules https://github.com/erpgap/alokai-odoo.git  ```
- Update Modules list so that it appears in the UI within Apps store
- Update Modules list so that it appears in the UI within Apps
- Look for the module within Apps and click on Install
- Spin up your store with the [Alokai-Odoo Integration](https://github.com/vuestorefront-community/odoo.git)
- Check [Alokai-Odoo Documentation] (https://docs.alokai.com/odoo/)

## Dependencies

OCA - Odoo Community Association - Base Graphql

## How to configure

- Go to Website -> Settings -> Vue Storefront
- Settings:
  - Payment Return Url
  - Cache Invalidation Url
  - Cache Invalidation Key
  - Web Base Url

## Support

To report a problem please [contact us](https://www.erpgap.com/page/contactus/).

Commercial support is available, please email [info@erpgap.com](info@erpgap.com)
or call tel:+351 917848501 for further information.

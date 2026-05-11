-- gold_marketplace_view
--
-- View materialised for direct consumption by marketplace sync
-- workers (eBay, Amazon, Google Shopping). The shape matches what
-- those catalog APIs accept on POST, minimising the marketplace
-- adapter code surface.

{{
  config(
    materialized='view',
    schema='gold',
    tags=['catalog', 'gold', 'marketplace']
  )
}}

select
    dealer_id,
    part_number                                AS sku,
    name_en                                    AS title,
    name_cn                                    AS title_cn,
    coalesce(retail_price, 0)                  AS price_usd,
    fitment                                    AS compatible_vehicles,
    last_seen_date                             AS last_updated
from {{ ref('gold_products_mart') }}
where name_en is not null
  and retail_price is not null

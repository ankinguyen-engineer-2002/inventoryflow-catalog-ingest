# Query 01 — Parts that fit a specific vehicle

The test specification's stated primary access pattern.

## Query

```sql
SELECT part_number, name_en, name_cn
FROM products
WHERE fitment @> '[{"make":"Kayo","model_code":"AY70-2"}]'
LIMIT 10;
```

## Index used

```
ix_products_fitment_gin
USING gin (fitment jsonb_path_ops)
size: 2,128 kB
```

## Latency (measured, 500 samples)

| Percentile | Time   |
| ---------- | ------ |
| p50        | 0.60 ms |
| p95        | 0.87 ms |
| p99        | 1.02 ms |
| max        | 1.32 ms |

## Sample output (10 rows)

```
 part_number    | name_en                            | name_cn
----------------+------------------------------------+------------------
 602006-0015    | black handle bar grip              | 把套
 602006-0026    | black handle bar grip(9.26.2022~)  | 把  套
 313001-0008    | multi-function switch              | 组合开关
 602001-0014    | black handle bar                   | 钢制方向把
 602017-0003    | handlebar foam                     | 护套芯
 313003-0011    | front stop switch                  | 熄火开关
 313003-0011-02 | front stop switch(new)             | 熄火开关
 602014-0036    | throttle cable(new)                | 油门线
 602011-0008-02 | accelerator                        | 加速器
 906003-0002    | plastic belt                       | 塑料扎带
```

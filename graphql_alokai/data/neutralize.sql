UPDATE ir_config_parameter SET value = 'changeme' WHERE key = 'alokai_cache_invalidation_key';
UPDATE ir_config_parameter SET value = false WHERE key = 'alokai_cache_invalidation';
UPDATE ir_config_parameter SET value = 'redis' WHERE key = 'alokai_redis_host';

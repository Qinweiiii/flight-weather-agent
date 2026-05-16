-- ============================================================
-- tb_dead_letter_order  死信订单表
-- 消费失败超过 maxRetry 次的消息落这里，人工处理或定时任务补偿
-- ============================================================
CREATE TABLE IF NOT EXISTS `tb_dead_letter_order` (
  `id`          bigint(20) NOT NULL AUTO_INCREMENT,
  `msg_id`      varchar(64) NOT NULL COMMENT 'Redis Stream 消息 ID',
  `order_id`    bigint(20) NOT NULL COMMENT '订单ID',
  `user_id`     bigint(20) NOT NULL COMMENT '用户ID',
  `voucher_id`  bigint(20) NOT NULL COMMENT '券ID',
  `retry_count` int NOT NULL DEFAULT 0 COMMENT '已重试次数',
  `last_error`  varchar(512) DEFAULT NULL COMMENT '最后一次错误信息',
  `status`      tinyint(1) NOT NULL DEFAULT 0 COMMENT '0待处理 1已修复 2已放弃',
  `create_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_msg_id` (`msg_id`),
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
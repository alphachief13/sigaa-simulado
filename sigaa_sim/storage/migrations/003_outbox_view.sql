-- 003: a outbox guarda o modelo da mensagem (view) além do texto simples,
-- para que HTML e texto sejam gerados do mesmo modelo na hora do envio.
ALTER TABLE outbox ADD COLUMN view_json TEXT;

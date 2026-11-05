create table users (
    id         serial primary key,
    name       VARCHAR(100) not null,
    created_at TIMESTAMPTZ not null default now()
);

create table devices(
    id  serial primary key,
    user_id integer not null references users(id),
    hostname  varchar(255) not null unique,
    os varchar(100) not null,
    cpu varchar(100),
    gpu varchar(100)not null,
    memory_gb integer not null check (memory_gb>0),
    created_at timestamptz not null default now()
);

create  table heartbeats(
    id serial primary key,
    device_id integer not null references devices(id),
    created_at timestamptz not null default now()
);

-- デバイスごとの最新の heartbeat を探す(GET /devices で毎回使う)ための索引。
-- エージェントは 30 秒ごとに 1 行足すので、行は増え続ける。索引がないと、全行を読むことになる
create index heartbeats_device_created on heartbeats (device_id, created_at desc);



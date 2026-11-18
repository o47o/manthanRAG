-- Migration 003: Add is_pinned to chat_messages
-- Run via Supabase SQL Editor or similar tool.
-- Schema: manthan_rag_working

SET search_path TO manthan_rag_working, public;

ALTER TABLE chat_messages 
ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN DEFAULT FALSE;

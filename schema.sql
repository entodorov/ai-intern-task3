-- 1. Създаване на таблицата за срещите
CREATE TABLE meetings (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  title TEXT NOT NULL,
  raw_transcript TEXT NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 2. Създаване на таблицата за бележките
CREATE TABLE notes (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  meeting_id UUID REFERENCES meetings(id) ON DELETE CASCADE,
  summary TEXT NOT NULL,
  action_items JSONB NOT NULL,
  decisions JSONB NOT NULL,
  llm TEXT DEFAULT 'gemini-2.5-flash',
  created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);
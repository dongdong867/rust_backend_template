CREATE TABLE tasks (
    id UUID PRIMARY KEY,
    title VARCHAR(200) NOT NULL CHECK (char_length(title) >= 1),
    status TEXT NOT NULL CHECK (status IN ('open', 'completed')),
    created_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    CONSTRAINT tasks_completion_consistent CHECK (
        (status = 'open' AND completed_at IS NULL)
        OR (status = 'completed' AND completed_at IS NOT NULL AND completed_at >= created_at)
    )
);

CREATE TRIGGER IF NOT EXISTS live_candidate_authorization_requires_exact_lineage
BEFORE INSERT ON live_candidate_signal_authorizations
WHEN NOT EXISTS (
    SELECT 1
    FROM live_candidate_signals AS candidate_signal
    JOIN live_candidates AS candidate
      ON candidate.id = candidate_signal.candidate_id
    JOIN live_signal_authorizations AS authorization
      ON authorization.authorization_id = NEW.authorization_id
    WHERE candidate_signal.candidate_id = NEW.candidate_id
      AND candidate_signal.signal_id = NEW.signal_id
      AND authorization.signal_id = NEW.signal_id
      AND authorization.adoption_decision_id = NEW.adoption_decision_id
      AND authorization.strategy_id = candidate.strategy_id
      AND authorization.strategy_version = candidate.strategy_version
)
BEGIN SELECT RAISE(ABORT, 'Candidate authorization lineage is inconsistent'); END;

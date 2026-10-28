"""Goal controls HTTP boundary: explicit pause / resume / switch. Orchestration metadata only.

Reuses the existing session dependency and the global CSRF middleware. Never confirms,
verifies, prepares or cancels anything; responses are re-read from authoritative views.
"""
from typing import Literal

from fastapi import Depends, Path
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from .conversation_goal_api import GOAL_ID
from .conversation_goal_control import GoalControls

KEY = dict(min_length=1, max_length=100)


class ControlBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_version: StrictInt = Field(ge=1)
    request_key: StrictStr = Field(**KEY)
    confirmed: StrictBool


def register_goal_controls(app, bank, loop, current_user):
    service = GoalControls(bank, loop.store)
    app.state.goal_controls = service
    access = app.state.goal_access  # set by register_goal_api; must be registered first

    def _scope(session):
        user = session['user_id']
        return user, session['token_hash'], access.epoch(user)

    @app.get('/api/goal-controls/paused')
    def goal_controls_paused_route(session=Depends(current_user)):
        user, token, epoch = _scope(session)
        # Service caps at LIST_LIMIT (100); ids only, public shape comes from loop.view.
        ids = [r['id'] for r in service.list_paused(user, token, epoch)]
        goals = [v for v in (loop.view(user, token, epoch, i) for i in ids) if v]
        return {'goals': goals}

    @app.get('/api/goal-controls/commands/{request_key}')
    def goal_controls_lookup_route(request_key: str = Path(**KEY), session=Depends(current_user)):
        user, token, epoch = _scope(session)
        # 404 means not found in this scope, not proof the command never ran.
        return {'command': service.lookup(user, token, epoch, request_key)}

    @app.post('/api/goal-controls/{goal_id}/{action}')
    def goal_controls_command_route(body: ControlBody,
                                    action: Literal['pause', 'resume', 'switch'],
                                    goal_id: str = Path(**GOAL_ID),
                                    session=Depends(current_user)):
        user, token, epoch = _scope(session)
        command = service.command(user, token, epoch, goal_id, action, body.expected_version,
                                  body.request_key, body.confirmed)
        # Committed; refresh from the authoritative current view, never a cached success.
        return {'command': command, 'goal': loop.view(user, token, epoch)}

    return service

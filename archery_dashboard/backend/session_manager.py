# backend/session_manager.py
import time
from typing import Optional, Dict, Any
from state import SessionState, Shot
import database
import screenshot
import config


class SessionManager:
    """
    Manages the lifecycle of archery training sessions.

    Responsibilities:
    - Track active session state
    - Coordinate screenshot capture on shot detection
    - Save shots to database
    - Auto-complete sessions when all arrows shot
    """

    def __init__(self):
        self.state: Optional[SessionState] = None
        self.active_session_id: Optional[int] = None

    def has_active_session(self) -> bool:
        """Check if there's an active session"""
        return self.state is not None and self.active_session_id is not None

    async def start_session(self, arrows_per_end: int, num_ends: int, notes: str = None) -> int:
        """
        Start a new training session.

        Args:
            arrows_per_end: Number of arrows per end
            num_ends: Number of ends in session
            notes: Optional notes for the session

        Returns:
            session_id: Database ID of the new session
        """
        # Create session in database
        session_id = await database.create_session(arrows_per_end, num_ends, notes)

        # Initialize state
        self.state = SessionState(
            session_id=session_id,
            start_time=time.time(),
            arrows_per_end=arrows_per_end,
            num_ends=num_ends
        )
        self.active_session_id = session_id

        print(f"[SESSION] Started session {session_id}: {arrows_per_end} arrows/end × {num_ends} ends")
        return session_id

    async def add_shot(
        self,
        shot: Shot,
        posture_data: Optional[Dict[str, Any]] = None
    ) -> Optional[int]:
        """
        Add a shot to the active session.

        Args:
            shot: Shot object with ts, x, y, r, score, is_x
            posture_data: Optional posture data with score and messages

        Returns:
            shot_id from database, or None if no active session
        """
        if not self.has_active_session():
            print("[SESSION] No active session, cannot add shot")
            return None

        # Add to in-memory state
        self.state.add_shot(shot)

        # Calculate end and shot numbers
        total_arrows = self.state.get_total_arrows()
        end_number = ((total_arrows - 1) // self.state.arrows_per_end) + 1
        shot_number = ((total_arrows - 1) % self.state.arrows_per_end) + 1

        # Capture screenshot
        screenshot_path = None
        try:
            screenshot_rel_path = f"session_{self.active_session_id}/shot_{total_arrows}.jpg"
            success = await screenshot.capture_screenshot_simple(
                config.STREAM_URL,
                screenshot_rel_path
            )
            if success:
                screenshot_path = screenshot_rel_path
        except Exception as e:
            print(f"[SESSION] Screenshot capture failed: {e}")

        # Extract posture data
        posture_score = None
        posture_messages = None
        if posture_data:
            posture_score = posture_data.get("score")
            posture_messages = posture_data.get("messages")

        # Save to database
        shot_id = await database.save_shot(
            session_id=self.active_session_id,
            end_number=end_number,
            shot_number=shot_number,
            timestamp=shot.ts,
            x=shot.x,
            y=shot.y,
            r=shot.r,
            score=shot.score,
            is_x=shot.is_x,
            screenshot_path=screenshot_path,
            posture_score=posture_score,
            posture_messages=posture_messages
        )

        print(f"[SESSION] Added shot {total_arrows} (end {end_number}, shot {shot_number}): score={shot.score}, is_x={shot.is_x}")

        # Check if session is complete
        if self.state.is_complete():
            await self.complete_and_save()

        return shot_id

    async def complete_and_save(self):
        """Mark the session as complete and save to database"""
        if not self.has_active_session():
            return

        end_time = time.time()
        total_score = self.state.get_total_score()
        total_arrows = self.state.get_total_arrows()

        # Update database
        await database.complete_session(
            session_id=self.active_session_id,
            end_time=end_time,
            total_score=total_score,
            total_arrows=total_arrows
        )

        # Update in-memory state
        self.state.end_time = end_time

        print(f"[SESSION] Session {self.active_session_id} completed: {total_score} points, {total_arrows} arrows")
        print(f"[SESSION] Duration: {end_time - self.state.start_time:.1f} seconds")

    async def end_session(self):
        """
        Manually end the session (even if incomplete).
        Useful for abandoning a session or early termination.
        """
        if not self.has_active_session():
            return

        if not self.state.is_complete():
            # Save as incomplete
            end_time = time.time()
            total_score = self.state.get_total_score()
            total_arrows = self.state.get_total_arrows()

            await database.complete_session(
                session_id=self.active_session_id,
                end_time=end_time,
                total_score=total_score,
                total_arrows=total_arrows
            )

            print(f"[SESSION] Session {self.active_session_id} ended early: {total_score} points, {total_arrows}/{self.state.arrows_per_end * self.state.num_ends} arrows")

        # Clear state
        self.state = None
        self.active_session_id = None

    def get_current_state(self) -> Optional[SessionState]:
        """Get the current session state"""
        return self.state

    def get_session_info(self) -> Optional[Dict[str, Any]]:
        """Get current session information"""
        if not self.has_active_session():
            return None

        return {
            "session_id": self.active_session_id,
            "start_time": self.state.start_time,
            "arrows_per_end": self.state.arrows_per_end,
            "num_ends": self.state.num_ends,
            "current_arrows": self.state.get_total_arrows(),
            "target_arrows": self.state.arrows_per_end * self.state.num_ends,
            "total_score": self.state.get_total_score(),
            "is_complete": self.state.is_complete(),
            "current_end": len(self.state.ends) if self.state.ends else 0,
            "arrows_in_current_end": len(self.state.ends[-1]) if self.state.ends else 0
        }

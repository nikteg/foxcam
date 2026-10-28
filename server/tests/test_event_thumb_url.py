from foxcam.models import Event
from foxcam.web.queries import event_thumb_url


def test_event_thumb_url_changes_with_its_box():
    def url(box, path="clips/c/event-3.jpg"):
        return event_thumb_url(Event(id=3, thumb_path=path, thumb_box=box))

    a, b = url([0.1, 0.2, 0.3, 0.4]), url([0.5, 0.2, 0.3, 0.4])
    assert a.startswith("/api/events/3/thumb?v=") and a != b
    assert a == url([0.1, 0.2, 0.3, 0.4])
    # Burned in (no box): the plain URL; no picture: none.
    assert url(None) == "/api/events/3/thumb"
    assert url(None, path=None) is None

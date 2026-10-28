// jsdom has no canvas: drawing is checked in a real browser (tests/test_web_settings.py), so a
// canvas here just has no context, like a browser with canvas off.
HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext

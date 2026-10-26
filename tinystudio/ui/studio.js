// Entry point: construct the application, then authenticate and render its route.
import { createApplication } from "./application.js?v=c96cb305d835";

const application = createApplication();
application.feedback.guard(() => application.start())();

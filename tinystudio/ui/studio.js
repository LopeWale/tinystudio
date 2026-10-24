// Entry point: construct the application, then authenticate and render its route.
import { createApplication } from "./application.js?v=82e74d4b5ecb";

const application = createApplication();
application.feedback.guard(() => application.start())();

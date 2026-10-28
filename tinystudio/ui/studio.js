// Entry point: construct the application, then authenticate and render its route.
import { createApplication } from "./application.js?v=c1757fa5eddd";

const application = createApplication();
application.feedback.guard(() => application.start())();

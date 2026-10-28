export class ConversationSync {
  constructor(options: any);
  start(): void;
  stop(): void;
  requestCheck(): void;
  notifySaved(revision: number): void;
}

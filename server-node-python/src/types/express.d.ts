import type { UploadContext } from '../services/document-storage';

declare global {
  namespace Express {
    interface Request {
      uploadContext?: UploadContext;
    }
  }
}

export {};

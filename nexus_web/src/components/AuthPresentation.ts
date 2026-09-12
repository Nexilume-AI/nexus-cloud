import { useApplicationDistribution } from '../app/distribution';
import { personalAuthPresentation, validateAuthPresentation } from '../app/authPresentation';

export function useAuthPresentation() {
  const selected = useApplicationDistribution().authPresentation;
  if (selected !== undefined) {
    validateAuthPresentation(selected);
    return selected;
  }
  return personalAuthPresentation;
}

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Save } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Loading } from '../../components/ui/Loading'
import { keys, useProfile } from '../../hooks/useStudioData'
import { PageHeader } from '../studio/PageHeader'

export function ProfilePage() {
  const query = useProfile(),
    client = useQueryClient(),
    [success, setSuccess] = useState('')
  const update = useMutation({
    mutationFn: api.profile.update,
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: keys.profile })
      await client.invalidateQueries({ queryKey: keys.overview })
      setSuccess('Profile saved.')
    },
  })
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    setSuccess('')
    update.mutate(Object.fromEntries(new FormData(e.currentTarget).entries()))
  }
  return (
    <>
      <PageHeader title="Profile" description="Personalize names, location, and language" />
      {query.isLoading ? (
        <Loading />
      ) : (
        <div className="page-content">
          <Card
            title="Assistant profile"
            description="These details are included in runtime instructions and visible throughout the interface."
          >
            <form className="card__body form-grid" onSubmit={submit}>
              <Field label="Your name" hint="How the assistant identifies and addresses you.">
                <input
                  name="user_name"
                  defaultValue={query.data?.user_name}
                  maxLength={80}
                  required
                />
              </Field>
              <Field label="Assistant name" hint="Shown in the interface and used in responses.">
                <input
                  name="assistant_name"
                  defaultValue={query.data?.assistant_name}
                  maxLength={80}
                  required
                />
              </Field>
              <Field full label="Location" hint="Used for weather, time, and nearby information.">
                <input
                  name="location"
                  defaultValue={query.data?.location}
                  maxLength={160}
                  required
                />
              </Field>
              <Field full label="Preferred response language">
                <select
                  name="preferred_language"
                  defaultValue={query.data?.preferred_language || 'auto'}
                >
                  <option value="auto">Automatic</option>
                  <option value="en">English</option>
                  <option value="fr">French</option>
                  <option value="ar">Arabic</option>
                </select>
              </Field>
              <div className="field--full">
                <Feedback
                  message={update.error instanceof Error ? update.error.message : success}
                  kind={success ? 'success' : 'error'}
                />
              </div>
              <div className="form-footer">
                <Button variant="primary" icon={<Save size={14} />} busy={update.isPending}>
                  Save profile
                </Button>
              </div>
            </form>
          </Card>
        </div>
      )}
    </>
  )
}
